"""Public errors: remote payloads are never included in exception messages."""


class FHIRError(RuntimeError):
    pass


class ConfigurationError(ValueError):
    pass


class SigningError(FHIRError):
    pass


class AuthenticationError(FHIRError):
    def __init__(self, message="Authentication failed.", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class TransportError(FHIRError):
    pass


class ProtocolError(FHIRError):
    pass


class PaginationError(FHIRError):
    """Iteration may have produced partial results before this exception."""


class FHIRHTTPError(FHIRError):
    def __init__(self, status_code, outcome=None, retry_after=None):
        super().__init__(f"FHIR request failed (HTTP {status_code}).")
        self.status_code = status_code
        # May contain patient data; available for explicit inspection, never logging.
        self.outcome = outcome
        self.retry_after = retry_after


class AuthorizationError(FHIRHTTPError):
    pass


class NotFoundError(FHIRHTTPError):
    pass


class RateLimitError(FHIRHTTPError):
    pass


class PatientContextError(FHIRError):
    pass
