"""Immutable, validated configuration; importing performs no I/O."""
import math
import os
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .errors import ConfigurationError


def validate_endpoint(url):
    try:
        parsed = urlsplit(url)
        port, path = parsed.port, unquote(parsed.path)
        valid = (parsed.scheme == "https" and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in url)
                 and "\\" not in url and "\\" not in path and "%" not in path
                 and not any(p in (".", "..") for p in path.split("/"))
                 and (port is None or port > 0))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ConfigurationError("Endpoints must be absolute HTTPS URLs without credentials, queries or fragments.")


@dataclass(frozen=True)
class Settings:
    client_id: str
    key_id: str = ""
    token_url: str = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
    base_url: str = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
    key_path: str = ""
    secret_id: str = ""
    region: str = "us-east-2"
    scopes: str = ""
    kms_key_id: str = ""
    timeout: float = 30
    max_retries: int = 2
    max_retry_delay: float = 30

    def __post_init__(self):
        if not isinstance(self.client_id, str) or not self.client_id.strip():
            raise ConfigurationError("A client ID is required.")
        for value in (self.client_id, self.key_id, self.key_path, self.secret_id, self.kms_key_id, self.region):
            if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise ConfigurationError("Client and signing configuration must contain valid strings.")
        validate_endpoint(self.token_url)
        validate_endpoint(self.base_url)
        if sum(bool(s) for s in (self.key_path, self.secret_id, self.kms_key_id)) > 1:
            raise ConfigurationError("Configure exactly one signing-key source.")
        if any((self.key_path, self.secret_id, self.kms_key_id)) and not self.key_id.strip():
            raise ConfigurationError("A JWT key ID is required for backend signing.")
        if not isinstance(self.scopes, str) or any(ord(c) < 32 for c in self.scopes):
            raise ConfigurationError("Scopes must be a space-separated string.")
        if not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 5:
            raise ConfigurationError("max_retries must be between zero and five.")
        for value in (self.timeout, self.max_retry_delay):
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ConfigurationError("Timeout and retry delay must be finite positive numbers.")

    def require_key_source(self):
        # Omit a source only when injecting a signer or app-managed user token.
        if sum(bool(s) for s in (self.key_path, self.secret_id, self.kms_key_id)) != 1:
            raise ConfigurationError("Configure exactly one private-key path, AWS secret ID or KMS key ID.")

    @classmethod
    def from_env(cls):
        settings = cls(
            client_id=os.environ.get("EPIC_CLIENT_ID", ""),
            key_id=os.environ.get("EPIC_KEY_ID", ""),
            token_url=os.environ.get("EPIC_TOKEN_URL") or cls.token_url,
            base_url=os.environ.get("EPIC_FHIR_BASE_URL") or cls.base_url,
            key_path=os.environ.get("EPIC_PRIVATE_KEY_PATH", ""),
            secret_id=os.environ.get("EPIC_PRIVATE_KEY_SECRET_ID", ""),
            kms_key_id=os.environ.get("EPIC_KMS_KEY_ID", ""),
            region=os.environ.get("AWS_REGION", "us-east-2"),
            scopes=os.environ.get("EPIC_SCOPES", ""),
        )
        settings.require_key_source()
        return settings
