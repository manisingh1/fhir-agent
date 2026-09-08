"""RS384 adapters. Construction performs no file or network I/O."""
import hashlib
import json
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jwt.algorithms import RSAAlgorithm

from .errors import ConfigurationError, SigningError


class Signer(Protocol):
    def sign(self, message: bytes) -> bytes: ...
    def public_jwk(self) -> dict: ...


def _public_jwk(key):
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ConfigurationError("RS384 requires an RSA key of at least 2048 bits.")
    return json.loads(RSAAlgorithm.to_jwk(key))


def _aws_client(service, region):
    try:
        import boto3
    except ImportError:
        raise ConfigurationError("Install fhir-agent[aws] to use AWS signing.") from None
    return boto3.client(service, region_name=region)


class PrivateKeySigner:
    def __init__(self, key):
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ConfigurationError("RS384 requires an RSA private key of at least 2048 bits.")
        self._key = key

    def sign(self, message):
        return self._key.sign(message, padding.PKCS1v15(), hashes.SHA384())

    def public_jwk(self):
        return _public_jwk(self._key.public_key())


class FileSigner:
    def __init__(self, path):
        self.path, self._signer = path, None

    def _pem(self):
        return Path(self.path).expanduser().read_bytes()

    def _load(self):
        if self._signer is None:
            try:
                key = serialization.load_pem_private_key(self._pem(), password=None)
                self._signer = PrivateKeySigner(key)
            except ConfigurationError:
                raise
            except Exception:
                raise SigningError("Could not load the configured RSA signing key.") from None
        return self._signer

    def sign(self, message):
        return self._load().sign(message)

    def public_jwk(self):
        return self._load().public_jwk()


class SecretsManagerSigner(FileSigner):
    def __init__(self, secret_id, region="us-east-2", client=None):
        self.secret_id, self.region, self._client = secret_id, region, client
        self._signer = None

    def _pem(self):
        if self._client is None:
            self._client = _aws_client("secretsmanager", self.region)
        value = self._client.get_secret_value(SecretId=self.secret_id)["SecretString"]
        if value.lstrip().startswith("{"):
            value = json.loads(value)["EPIC_PRIVATE_KEY"]
        return value.encode()


class KMSSigner:
    algorithm = "RSASSA_PKCS1_V1_5_SHA_384"

    def __init__(self, key_id, region="us-east-2", client=None):
        self.key_id, self.region, self._client = key_id, region, client
        self._jwk = None

    @property
    def client(self):
        if self._client is None:
            self._client = _aws_client("kms", self.region)
        return self._client

    def sign(self, message):
        try:
            result = self.client.sign(
                KeyId=self.key_id, Message=hashlib.sha384(message).digest(),
                MessageType="DIGEST", SigningAlgorithm=self.algorithm,
            )
            if result["SigningAlgorithm"] != self.algorithm:
                raise SigningError("KMS returned an unexpected signing algorithm.")
            return result["Signature"]
        except (ConfigurationError, SigningError):
            raise
        except Exception:
            raise SigningError("KMS could not sign the client assertion.") from None

    def public_jwk(self):
        if self._jwk is None:
            try:
                result = self.client.get_public_key(KeyId=self.key_id)
                if result["KeyUsage"] != "SIGN_VERIFY" or self.algorithm not in result["SigningAlgorithms"]:
                    raise ConfigurationError("KMS key must support RS384 signing.")
                self._jwk = _public_jwk(serialization.load_der_public_key(result["PublicKey"]))
            except ConfigurationError:
                raise
            except Exception:
                raise SigningError("Could not retrieve the KMS public key.") from None
        return dict(self._jwk)


def signer_from_settings(settings):
    settings.require_key_source()
    if settings.kms_key_id:
        return KMSSigner(settings.kms_key_id, settings.region)
    if settings.secret_id:
        return SecretsManagerSigner(settings.secret_id, settings.region)
    return FileSigner(settings.key_path)


def load_private_key(settings):
    """Compatibility helper; KMS private keys cannot be exported."""
    signer = signer_from_settings(settings)
    if isinstance(signer, KMSSigner):
        raise ConfigurationError("KMS private keys cannot be exported; use a signer.")
    return signer._load()._key


def public_jwks(signer, key_id):
    jwk = signer.public_jwk()
    jwk.update(kid=key_id, use="sig", alg="RS384")
    return {"keys": [jwk]}
