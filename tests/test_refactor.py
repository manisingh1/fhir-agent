import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from fhir_agent import BackendAuth, ClinicalClient, FHIRClient, PatientContext, Settings, UserTokenAuth
from fhir_agent.auth import create_jwt_token
from fhir_agent.cli import main
from fhir_agent.errors import (AuthenticationError, AuthorizationError, ConfigurationError,
                               NotFoundError, PaginationError, PatientContextError,
                               ProtocolError, RateLimitError, SigningError, TransportError)
from fhir_agent.signing import (FileSigner, KMSSigner, PrivateKeySigner, SecretsManagerSigner,
                                public_jwks, signer_from_settings)


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def config(**kwargs):
    return Settings(client_id="test-client", key_id="test-key", **kwargs)


def response(body, status=200, headers=None):
    r = Mock(status_code=status, headers=headers or {})
    r.json.return_value = body
    return r


def make_client(responses, **kwargs):
    session, auth, sleep = Mock(), Mock(), Mock()
    session.get.side_effect = responses
    auth.token.return_value = "test-token"
    client = FHIRClient(config(**kwargs), auth, session, sleeper=sleep)
    return client, auth, session, sleep


def bundle(entries=(), **kwargs):
    return {"resourceType": "Bundle", "type": "searchset", "entry": list(entries), **kwargs}


@pytest.mark.parametrize("url", ["http://example.org/fhir", "https:///fhir", "https://user:pass@host/fhir",
                                 "https://host:bad/fhir", "https://host/fhir?x=1", "https://host/fhir#x",
                                 "https://host/a/../fhir", "https://host/a/%252e%252e/fhir"])
def test_direct_settings_validate_urls(url):
    with pytest.raises(ConfigurationError):
        config(base_url=url)


def test_settings_validate_sources_and_immutability():
    with pytest.raises(ConfigurationError):
        config(key_path="key.pem", kms_key_id="key")
    with pytest.raises(ConfigurationError):
        config(timeout=float("nan"))
    with pytest.raises(ConfigurationError):
        signer_from_settings(config())
    with pytest.raises(AttributeError):
        config().base_url = "https://other.example/"


def test_kms_jwt_verified_with_real_rsa_public_key(key):
    kms = Mock()
    def sign(**kwargs):
        assert kwargs["MessageType"] == "DIGEST"
        assert kwargs["SigningAlgorithm"] == "RSASSA_PKCS1_V1_5_SHA_384"
        assert kwargs["KeyId"] == "kms-key"
        assert len(kwargs["Message"]) == 48
        return {"SigningAlgorithm": kwargs["SigningAlgorithm"], "Signature": key.sign(
            kwargs["Message"], padding.PKCS1v15(), utils.Prehashed(hashes.SHA384()))}
    kms.sign.side_effect = sign
    kms.get_public_key.return_value = {
        "PublicKey": key.public_key().public_bytes(serialization.Encoding.DER,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo),
        "KeyUsage": "SIGN_VERIFY", "SigningAlgorithms": [KMSSigner.algorithm]}
    signer = KMSSigner("kms-key", client=kms)
    token = create_jwt_token(config(), signer=signer)
    claims = jwt.decode(token, key.public_key(), audience=config().token_url, algorithms=["RS384"])
    assert claims["sub"] == "test-client"
    message = token.rsplit(".", 1)[0].encode()
    assert kms.sign.call_args.kwargs["Message"] == hashlib.sha384(message).digest()
    jwks = public_jwks(signer, "test-key")
    assert jwks["keys"][0]["kid"] == "test-key"
    assert not ({"d", "p", "q", "dp", "dq", "qi"} & jwks["keys"][0].keys())
    signer.public_jwk()
    assert kms.get_public_key.call_count == 1


def test_signing_errors_do_not_leak_aws_payload():
    kms = Mock()
    kms.sign.side_effect = RuntimeError("PRIVATE_SECRET")
    with pytest.raises(SigningError) as caught:
        KMSSigner("key", client=kms).sign(b"message")
    assert "PRIVATE_SECRET" not in str(caught.value)


def test_file_and_secrets_signers_cache_and_export_only_public_data(key, tmp_path):
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path = tmp_path / "test.pem"
    path.write_bytes(pem)
    file_signer = FileSigner(path)
    public = public_jwks(file_signer, "test-key")
    path.unlink()  # The parsed key is cached, rather than re-read at each refresh.
    signed = create_jwt_token(config(), signer=file_signer)
    jwt.decode(signed, key.public_key(), algorithms=["RS384"], audience=config().token_url)
    aws = Mock()
    aws.get_secret_value.return_value = {"SecretString": json.dumps({"EPIC_PRIVATE_KEY": pem.decode()})}
    signer = SecretsManagerSigner("secret-id", client=aws)
    assert public_jwks(signer, "test-key") == public
    signer.sign(b"one")
    signer.sign(b"two")
    assert aws.get_secret_value.call_count == 1


def test_concurrent_token_refresh_performs_one_exchange(key):
    session, entered, release = Mock(), threading.Event(), threading.Event()
    def exchange(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response({"access_token": "shared", "expires_in": 3600, "scope": "system/Patient.read"})
    session.post.side_effect = exchange
    auth = BackendAuth(config(), session, signer=PrivateKeySigner(key))
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(auth.token) for _ in range(8)]
        assert entered.wait(5)
        release.set()
        assert [f.result() for f in futures] == ["shared"] * 8
    assert session.post.call_count == 1
    assert auth.granted_scopes == "system/Patient.read"
    auth.invalidate("different-older-token")
    assert auth.token() == "shared"
    assert session.post.call_count == 1


@pytest.mark.parametrize("body", [{}, [], {"access_token": "secret\r\nx", "expires_in": 100},
                                  {"access_token": "secret", "expires_in": "nan"},
                                  {"access_token": "secret", "expires_in": 100, "token_type": "Basic"}])
def test_malformed_token_response_is_safe(body, key):
    session = Mock()
    session.post.return_value = response(body)
    with pytest.raises(AuthenticationError) as caught:
        BackendAuth(config(), session, signer=PrivateKeySigner(key)).token()
    assert "secret" not in str(caught.value)


def test_user_tokens_are_session_scoped_and_never_fall_back():
    now = [0]
    first = UserTokenAuth("first", 5, clock=lambda: now[0])
    second = UserTokenAuth("second", 50, clock=lambda: now[0])
    first.invalidate("first")
    with pytest.raises(AuthenticationError):
        first.token()
    assert second.token() == "second"
    now[0] = 51
    with pytest.raises(AuthenticationError):
        second.token()


def test_user_client_needs_no_signing_configuration():
    settings = Settings(client_id="user-app")
    session = Mock()
    session.get.return_value = response({"resourceType": "Patient", "id": "123"})
    client = FHIRClient(settings, UserTokenAuth("user-token", 300), session)
    assert client.get("Patient/123")["id"] == "123"
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer user-token"
    signer = Mock()
    with pytest.raises(ConfigurationError):
        create_jwt_token(settings, signer=signer)
    signer.sign.assert_not_called()


def test_token_that_expires_during_exchange_is_not_returned(key):
    now = [0]
    session = Mock()
    def exchange(*args, **kwargs):
        now[0] += 10
        return response({"access_token": "expired", "expires_in": 5})
    session.post.side_effect = exchange
    auth = BackendAuth(config(), session, signer=PrivateKeySigner(key), clock=lambda: now[0])
    with pytest.raises(AuthenticationError, match="expired"):
        auth.token()


def test_401_refreshes_once_and_403_does_not_retry():
    client, auth, session, sleep = make_client([response({}, 401), response({}, 401)])
    auth.token.side_effect = ["old", "new"]
    with pytest.raises(AuthenticationError):
        client.get("Patient/123")
    auth.invalidate.assert_called_once_with("old")
    assert session.get.call_count == 2
    sleep.assert_not_called()
    client, auth, session, _ = make_client([response({}, 403)])
    with pytest.raises(AuthorizationError):
        client.get("Patient/123")
    auth.invalidate.assert_not_called()
    assert session.get.call_count == 1


def test_retries_respect_retry_after_and_are_bounded():
    client, _, session, sleep = make_client([response({}, 429, {"Retry-After": "3"}),
                                            response({}, 503), response({"id": "ok"})])
    assert client.get("Patient/123") == {"id": "ok"}
    assert sleep.call_args_list[0].args == (3,)
    assert session.get.call_count == 3
    client, _, session, sleep = make_client([response({}, 429, {"Retry-After": "999"})])
    with pytest.raises(RateLimitError) as error:
        client.get("Patient/123")
    assert error.value.retry_after == 999
    sleep.assert_not_called()
    client, _, session, sleep = make_client([requests.Timeout("sensitive-url")] * 3)
    with pytest.raises(TransportError) as error:
        client.get("Patient/123")
    assert "sensitive-url" not in str(error.value)
    assert session.get.call_count == 3


def test_operation_outcome_available_but_not_in_error_text():
    outcome = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "PATIENT_SECRET"}]}
    client, _, _, _ = make_client([response(outcome, 404)])
    with pytest.raises(NotFoundError) as error:
        client.get("Patient/123")
    assert error.value.outcome == outcome
    assert "PATIENT_SECRET" not in repr(error.value)


def test_search_preserves_metadata_modes_warnings_and_repeated_params():
    included = {"resourceType": "Practitioner", "id": "doctor"}
    outcome = {"resourceType": "OperationOutcome", "issue": [{"severity": "warning", "diagnostics": "PRIVATE"}]}
    page = bundle([
        {"fullUrl": "https://ehr.example/Observation/one", "search": {"mode": "match"},
         "resource": {"resourceType": "Observation", "id": "one"}},
        {"search": {"mode": "include"}, "resource": included},
        {"search": {"mode": "outcome"}, "resource": outcome},
    ], total=2, id="bundle-id", timestamp="2026-09-05T00:00:00Z",
        link=[{"relation": "next", "url": "?page=2"}])
    client, _, session, _ = make_client([response(page), response(bundle([
        {"resource": {"resourceType": "Observation", "id": "two"}}]))])
    params = [("date", "ge2026-01-01"), ("date", "lt2026-09-01")]
    result = client.search_result("Observation", params)
    assert [r["id"] for r in result.matches] == ["one", "two"]
    assert result.included == [included]
    assert result.outcomes == [outcome]
    assert not result.has_errors
    assert result.pages[0].total == 2
    assert result.pages[0].entries[0].full_url == "https://ehr.example/Observation/one"
    assert result.pages[0].bundle_id == "bundle-id"
    assert result.traversal_complete
    assert "PRIVATE" not in repr(result.pages[0].entries[-1])
    assert session.get.call_args_list[0].kwargs["params"] == params
    assert session.get.call_args_list[1].args[0].endswith("/Observation?page=2")
    assert session.get.call_args_list[1].kwargs["params"] is None


@pytest.mark.parametrize("issues, expected", [([{"severity": "error"}], True),
                                            ([{"severity": "fatal"}], True),
                                            (None, False), (7, False)])
def test_search_errors_are_visible_even_when_all_pages_were_fetched(issues, expected):
    outcome = {"resourceType": "OperationOutcome", "issue": issues}
    client, _, _, _ = make_client([response(bundle([{"resource": outcome}]))])
    result = client.search_result("Observation")
    assert result.traversal_complete
    assert result.has_errors is expected
    assert result.matches == []
    assert result.outcomes == [outcome]


@pytest.mark.parametrize("path", ["Observation/../Patient", "%252e%252e/token", "Patient/%0afoo", "Patient\\123"])
def test_unsafe_paths_rejected_before_auth(path):
    client, auth, session, _ = make_client([])
    with pytest.raises(ValueError):
        client.get(path)
    auth.token.assert_not_called()
    session.get.assert_not_called()


def test_cross_host_next_link_and_loop_do_not_produce_complete_results():
    for next_url, error in [("https://other.example/Patient", ValueError), ("Patient", PaginationError)]:
        client, auth, session, _ = make_client([response(bundle(link=[{"relation": "next", "url": next_url}]))])
        with pytest.raises(error):
            client.search_result("Patient")
        assert session.get.call_count == 1


@pytest.mark.parametrize("page", [bundle(type="transaction"), bundle(total=-1), bundle(entry=[{"resource": []}]),
                                  bundle(link=[{"relation": "next"}])])
def test_malformed_search_bundle_rejected(page):
    client, _, _, _ = make_client([response(page)])
    with pytest.raises(ProtocolError):
        client.search_result("Patient")


def test_clinical_context_cannot_be_overridden_or_expanded():
    transport = Mock()
    clinical = ClinicalClient(transport, PatientContext("123", frozenset({"Condition"})))
    with pytest.raises(PatientContextError):
        clinical.get_conditions("456")
    with pytest.raises(PatientContextError):
        clinical.get_labs()
    transport.search_result.assert_not_called()


def test_labs_enforce_patient_and_date_and_return_full_result():
    entry = {"resource": {"resourceType": "Observation", "id": "lab", "subject": {"reference": "Patient/123"}}}
    client, _, session, _ = make_client([response(bundle([entry]))])
    clinical = ClinicalClient(client, PatientContext("123", frozenset({"Observation"})))
    result = clinical.get_labs(since="2026-01-01")
    assert result.matches[0]["id"] == "lab"
    assert session.get.call_args.kwargs["params"] == [("patient", "123"), ("category", "laboratory"), ("date", "ge2026-01-01")]
    with pytest.raises(PatientContextError):
        clinical.get_labs(since="2026-02-31")
    assert session.get.call_count == 1


def test_wrong_patient_in_server_response_is_not_returned():
    client, _, _, _ = make_client([response(bundle([{"resource": {
        "resourceType": "Condition", "subject": {"reference": "Patient/456"}}}]))])
    clinical = ClinicalClient(client, PatientContext("123", frozenset({"Condition"})))
    with pytest.raises(PatientContextError):
        clinical.get_conditions()


def test_cli_jwks_does_not_export_private_key(key, tmp_path, monkeypatch, capsys):
    path = tmp_path / "key.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EPIC_CLIENT_ID", "test-client")
    monkeypatch.setenv("EPIC_KEY_ID", "test-key")
    monkeypatch.setenv("EPIC_PRIVATE_KEY_PATH", str(path))
    monkeypatch.delenv("EPIC_PRIVATE_KEY_SECRET_ID", raising=False)
    monkeypatch.delenv("EPIC_KMS_KEY_ID", raising=False)
    assert main(["jwks"]) == 0
    jwk = json.loads(capsys.readouterr().out)["keys"][0]
    assert set(jwk) == {"kty", "key_ops", "n", "e", "kid", "use", "alg"}


def test_cli_metadata_and_safe_errors(capsys):
    result = FHIRClient._page(bundle(total=0), "https://ehr.example/Patient")
    from fhir_agent.client import SearchResult
    with patch("fhir_agent.cli.Settings.from_env", return_value=config()), patch("fhir_agent.cli.FHIRClient") as constructor:
        client = constructor.return_value.__enter__.return_value
        client.search_result.return_value = SearchResult((result,))
        assert main(["search", "Patient", "--with-metadata"]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["traversal_complete"] is True
        assert output["pages"][0]["total"] == 0
        client.get.side_effect = RuntimeError("PRIVATE_SECRET")
        assert main(["get", "Patient/123"]) == 1
        assert "PRIVATE_SECRET" not in capsys.readouterr().err
