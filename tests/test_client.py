from unittest.mock import Mock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from fhir_agent.auth import BackendAuth, Settings, create_jwt_token
from fhir_agent.fhir import FHIRClient


def settings():
    return Settings(client_id="test-client", key_id="test-key")


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


def test_assertion_signature_claims_and_uniqueness():
    config = settings()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    first, second = [create_jwt_token(config, key) for _ in range(2)]
    payload = jwt.decode(first, key.public_key(), algorithms=["RS384"], audience=config.token_url)
    assert payload["iss"] == payload["sub"] == config.client_id
    assert payload["exp"] - payload["iat"] == 60
    assert jwt.get_unverified_header(first)["kid"] == config.key_id
    assert payload["jti"] != jwt.decode(second, key.public_key(), algorithms=["RS384"], audience=config.token_url)["jti"]


@patch("fhir_agent.auth.create_jwt_token", return_value="assertion")
def test_token_cache_refresh_and_safe_failure(_):
    session = Mock()
    session.post.side_effect = [response({"access_token": "one", "expires_in": 3600}),
                                response({"access_token": "two", "expires_in": 3600}),
                                response({"secret": "do-not-print"}, 401)]
    auth = BackendAuth(settings(), session)
    assert auth.token() == auth.token() == "one"
    assert session.post.call_count == 1
    auth._expires = 0
    assert auth.token() == "two"
    auth._expires = 0
    with pytest.raises(RuntimeError, match="HTTP 401") as exc:
        auth.token()
    assert "do-not-print" not in str(exc.value)


def client_with_pages(pages):
    session = Mock()
    session.get.side_effect = [response(page) for page in pages]
    return FHIRClient(settings(), auth=Mock(token=Mock(return_value="token")), session=session)


def test_pagination_and_parameters():
    first = {"resourceType": "Bundle", "entry": [{"resource": {"id": "a"}}],
             "link": [{"relation": "next", "url": settings().base_url + "/Patient?page=2"}]}
    client = client_with_pages([first, {"resourceType": "Bundle", "entry": [{"resource": {"id": "b"}}]}])
    assert list(client.search("Patient", {"name": "test"})) == [{"id": "a"}, {"id": "b"}]
    calls = client.session.get.call_args_list
    assert calls[0].kwargs["params"] == {"name": "test"}
    assert calls[1].kwargs["params"] is None
    assert calls[1].kwargs["allow_redirects"] is False


@pytest.mark.parametrize("path", ["https://evil.example/Patient", "//evil.example/Patient", "../token", "%2e%2e/token", "/outside"])
def test_disallowed_links_never_receive_token(path):
    client = client_with_pages([])
    with pytest.raises(ValueError):
        client.get(path)
    client.auth.token.assert_not_called()
    client.session.get.assert_not_called()


def test_page_limit_is_explicit():
    client = client_with_pages([{"resourceType": "Bundle", "link": [{"relation": "next", "url": "Patient?page=2"}]}])
    with pytest.raises(RuntimeError, match="page limit"):
        list(client.search("Patient", max_pages=1))


def test_redirect_not_followed():
    client = client_with_pages([])
    client.session.get.side_effect = None
    client.session.get.return_value = response({}, 302)
    with pytest.raises(RuntimeError, match="HTTP 302"):
        client.get("Patient")


def test_settings_reject_ambiguous_key_sources(monkeypatch):
    monkeypatch.setenv("EPIC_CLIENT_ID", "test")
    monkeypatch.setenv("EPIC_KEY_ID", "key")
    monkeypatch.setenv("EPIC_PRIVATE_KEY_PATH", "/test.pem")
    monkeypatch.setenv("EPIC_PRIVATE_KEY_SECRET_ID", "test-secret")
    with pytest.raises(ValueError, match="exactly one"):
        Settings.from_env()
