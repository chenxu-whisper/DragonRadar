"""Fixed public errors without credential or database details."""


class _OwnerError(Exception):
    status_code: int
    code: str
    message: str

    def __init__(self):
        super().__init__(self.message)


class InvalidPairing(_OwnerError):
    status_code = 401
    code = "invalid_pairing"
    message = "Invalid pairing"


class SessionUnauthorized(_OwnerError):
    status_code = 401
    code = "session_unauthorized"
    message = "Owner session required"


class CsrfForbidden(_OwnerError):
    status_code = 403
    code = "csrf_forbidden"
    message = "Invalid CSRF token"


class PairingRateLimited(_OwnerError):
    status_code = 429
    code = "pairing_rate_limited"
    message = "Pairing rate limit exceeded"
