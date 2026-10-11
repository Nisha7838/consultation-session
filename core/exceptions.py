from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "domain_error"

    def __init__(self, detail, code=None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code


class InvalidTransition(DomainError):
    code = "invalid_transition"


class InsufficientBalance(DomainError):
    code = "insufficient_balance"


class ActiveSessionExists(DomainError):
    code = "active_session_exists"


class NotAllowed(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "not_allowed"


class NotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response(
            {"error": exc.code, "detail": exc.detail}, status=exc.status_code
        )
    return exception_handler(exc, context)
