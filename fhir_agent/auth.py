"""Token providers for service authentication and app-managed SMART sessions."""
import json
import math
import threading
import time
import uuid
from typing import Protocol

import requests
from jwt.utils import base64url_encode

from .config import Settings  # backwards-compatible import
from .errors import AuthenticationError, ConfigurationError
from .signing import PrivateKeySigner, load_private_key, signer_from_settings


class TokenProvider(Protocol):
    def token(self) -> str: ...
    def invalidate(self, rejected_token: str) -> None: ...


def create_jwt_token(settings, key=None, *, signer=None):
    if not settings.key_id.strip():
        raise ConfigurationError("A JWT key ID is required for backend signing.")
    if key is not None and signer is not None:
        raise ConfigurationError("Supply a private key or a signer, not both.")
    signer = signer or (PrivateKeySigner(key) if key is not None else signer_from_settings(settings))
    now = int(time.time())
    claims = {"iss": settings.client_id, "sub": settings.client_id,
              "aud": settings.token_url, "jti": str(uuid.uuid4()),
              "iat": now, "nbf": now, "exp": now + 60}
    header = {"typ": "JWT", "alg": "RS384", "kid": settings.key_id}
    message = b".".join(base64url_encode(json.dumps(v, separators=(",", ":")).encode())
                        for v in (header, claims))
    return (message + b"." + base64url_encode(signer.sign(message))).decode("ascii")


def _valid_token(token):
    return isinstance(token, str) and bool(token) and all(33 <= ord(c) <= 126 for c in token)


class BackendAuth:
    def __init__(self, settings, session=None, *, signer=None, clock=time.monotonic):
        self.settings = settings
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._signer = signer
        self._clock = clock
        self._lock = threading.Lock()
        self._token = None
        self._expires = 0
        self.granted_scopes = ""

    def invalidate(self, rejected_token):
        with self._lock:
            if self._token == rejected_token:
                self._token = None
                self._expires = 0

    def token(self):
        with self._lock:
            if self._token and self._clock() < self._expires:
                return self._token
            started = self._clock()
            if self._signer is None and any((self.settings.key_path, self.settings.secret_id,
                                            self.settings.kms_key_id)):
                self._signer = signer_from_settings(self.settings)
            assertion = (create_jwt_token(self.settings, signer=self._signer)
                         if self._signer is not None else create_jwt_token(self.settings))
            payload = {"grant_type": "client_credentials",
                       "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                       "client_assertion": assertion}
            if self.settings.scopes:
                payload["scope"] = self.settings.scopes
            try:
                response = self.session.post(self.settings.token_url, data=payload,
                                             timeout=self.settings.timeout, allow_redirects=False)
            except requests.RequestException:
                raise AuthenticationError("Token endpoint could not be reached.") from None
            if response.status_code != 200:
                raise AuthenticationError(f"Token request failed (HTTP {response.status_code}).", response.status_code)
            try:
                result = response.json()
                token = result["access_token"]
                lifetime = float(result.get("expires_in", 0))
                scope = result.get("scope", "")
                if (not _valid_token(token) or not math.isfinite(lifetime) or lifetime <= 0
                        or not isinstance(scope, str)
                        or str(result.get("token_type", "Bearer")).lower() != "bearer"):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AuthenticationError("Token endpoint returned an invalid token response.") from None
            self._expires = started + max(0, lifetime - min(30, lifetime / 10))
            if self._clock() >= started + lifetime:
                raise AuthenticationError("Token expired before the token request completed.")
            self._token, self.granted_scopes = token, scope
            return token

    def close(self):
        if self._owns_session:
            self.session.close()


class UserTokenAuth:
    """One app-managed SMART session. Never share this provider across users.

    The host app performs the OAuth launch, code exchange, and refresh. Construct
    a new provider after refreshing; this adapter never falls back to system auth.
    """
    def __init__(self, access_token, expires_in, scopes="", *, clock=time.monotonic):
        if (not _valid_token(access_token) or not isinstance(expires_in, (int, float))
                or not math.isfinite(expires_in) or expires_in <= 0 or not isinstance(scopes, str)):
            raise ConfigurationError("A valid user access token and lifetime are required.")
        self._token = access_token
        self._clock = clock
        self._expires = clock() + expires_in
        self.granted_scopes = scopes
        self._lock = threading.Lock()

    def token(self):
        with self._lock:
            if self._token is None or self._clock() >= self._expires:
                raise AuthenticationError("The user session has expired or was rejected; reauthorize or refresh it.")
            return self._token

    def invalidate(self, rejected_token):
        with self._lock:
            if self._token == rejected_token:
                self._token = None
