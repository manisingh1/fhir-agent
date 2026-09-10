import io
import json
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from fhir_agent.signing import PrivateKeySigner, public_jwks
from scripts.publish_jwks import PublicationError, merge_jwks, publish, validate_jwks, validate_manifest, verify_public_url


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def manifest():
    return {"account_id": "123456789012", "region": "us-east-2", "bucket": "fhir-test-jwks",
            "object_key": "jwks.json", "distribution_id": "EXAMPLE123",
            "jwks_url": "https://example123.cloudfront.net/jwks.json",
            "keys": [{"kid": "sandbox-v1", "arn": "arn:aws:kms:us-east-2:123456789012:key/12345678-1234-1234-1234-123456789012"}]}


def services(rsa_key, manifest, existing=None):
    aws, http = Mock(), Mock()
    clients = {name: Mock() for name in ("sts", "kms", "s3", "cloudfront")}
    aws.client.side_effect = lambda name, **kwargs: clients[name]
    clients["sts"].get_caller_identity.return_value = {"Account": manifest["account_id"]}
    clients["kms"].get_public_key.return_value = {
        "KeyUsage": "SIGN_VERIFY", "SigningAlgorithms": ["RSASSA_PKCS1_V1_5_SHA_384"],
        "PublicKey": rsa_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)}
    clients["s3"].list_objects_v2.return_value = {"Contents": [{"Key": "jwks.json"}]} if existing else {}
    if existing:
        clients["s3"].get_object.return_value = {"Body": io.BytesIO(json.dumps(existing).encode()), "ETag": '"old-etag"'}
    clients["cloudfront"].create_invalidation.return_value = {"Invalidation": {"Id": "I123"}}
    return aws, clients, http


def public_response(http, jwks, status=200):
    response = Mock(status_code=status, headers={"Content-Type": "application/json"})
    response.iter_content.return_value = [json.dumps(jwks).encode()]
    http.get.return_value.__enter__ = Mock(return_value=response)
    http.get.return_value.__exit__ = Mock(return_value=False)


@pytest.mark.parametrize("field,value", [("jwks_url", "https://attacker.example/jwks.json"),
                                       ("object_key", "private.json"), ("account_id", "wrong"),
                                       ("keys", [{"arn": "alias/key", "kid": "one"}])])
def test_manifest_rejected_before_aws(field, value, manifest):
    manifest[field] = value
    aws = Mock()
    with pytest.raises(PublicationError):
        publish(manifest, aws, Mock(), apply=True)
    aws.client.assert_not_called()


def test_mismatched_account_rejected_before_key_access(rsa_key, manifest):
    aws, clients, http = services(rsa_key, manifest)
    clients["sts"].get_caller_identity.return_value = {"Account": "999999999999"}
    with pytest.raises(PublicationError):
        publish(manifest, aws, http, apply=True)
    clients["kms"].get_public_key.assert_not_called()
    clients["s3"].put_object.assert_not_called()


def test_private_key_fields_cannot_be_published(rsa_key):
    jwks = public_jwks(PrivateKeySigner(rsa_key), "one")
    jwks["keys"][0]["d"] = "PRIVATE"
    with pytest.raises(PublicationError):
        validate_jwks(jwks)


def test_rotation_preserves_old_keys_and_requires_explicit_retirement(rsa_key):
    old = public_jwks(PrivateKeySigner(rsa_key), "old")
    new = public_jwks(PrivateKeySigner(rsa_key), "new")
    assert len(merge_jwks(old, new)["keys"]) == 2
    assert merge_jwks(old, new, ["old"]) == new
    with pytest.raises(PublicationError):
        merge_jwks(old, new, ["new"])
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(PublicationError):
        merge_jwks(old, public_jwks(PrivateKeySigner(other), "old"))


def test_default_publish_is_read_only(rsa_key, manifest):
    aws, clients, http = services(rsa_key, manifest)
    assert publish(manifest, aws, http)["applied"] is False
    clients["s3"].put_object.assert_not_called()
    clients["cloudfront"].create_invalidation.assert_not_called()
    clients["kms"].sign.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_publication_uses_conditional_write_and_verifies_endpoint(rsa_key, manifest, existing):
    jwks = public_jwks(PrivateKeySigner(rsa_key), "sandbox-v1")
    aws, clients, http = services(rsa_key, manifest, jwks if existing else None)
    public_response(http, jwks)
    result = publish(manifest, aws, http, apply=True)
    assert result["verified"]
    put = clients["s3"].put_object.call_args.kwargs
    assert put.get("IfMatch") == ('"old-etag"' if existing else None)
    assert put.get("IfNoneMatch") == (None if existing else "*")
    assert put["ExpectedBucketOwner"] == manifest["account_id"]
    assert put["Key"] == "jwks.json" and put["ServerSideEncryption"] == "AES256"
    assert json.loads(put["Body"]) == jwks
    assert http.get.call_args.kwargs["allow_redirects"] is False
    clients["kms"].sign.assert_not_called()


def test_failed_upload_never_reports_success_or_invalidates(rsa_key, manifest):
    aws, clients, http = services(rsa_key, manifest)
    clients["s3"].put_object.side_effect = RuntimeError("precondition failed")
    with pytest.raises(RuntimeError):
        publish(manifest, aws, http, apply=True)
    clients["cloudfront"].create_invalidation.assert_not_called()
    http.get.assert_not_called()


def test_wrong_public_document_fails_verification(rsa_key):
    http = Mock()
    jwks = public_jwks(PrivateKeySigner(rsa_key), "expected")
    public_response(http, {"keys": []})
    with pytest.raises(PublicationError):
        verify_public_url("https://example.cloudfront.net/jwks.json", jwks, http, attempts=1)
