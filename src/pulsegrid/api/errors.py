"""Translation of exceptions into the JSON error envelope."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from pulsegrid.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InfrastructureError,
    NotFoundError,
    PulseGridError,
    ValidationFailedError,
)
from pulsegrid.core.logging import get_logger

logger = get_logger(__name__)

_STATUS_BY_ERROR: tuple[tuple[type[PulseGridError], int], ...] = (
    (ValidationFailedError, 422),
    (AuthenticationError, 401),
    (NotFoundError, 404),
    (ConflictError, 409),
    (InfrastructureError, 503),
)


def status_for(error: PulseGridError) -> int:
    """HTTP status code for a PulseGrid error."""
    return next((status for kind, status in _STATUS_BY_ERROR if isinstance(error, kind)), 500)


def _request_id(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else None


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Build the shared ``{"error": {...}}`` response."""
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": _request_id(request),
    }
    if details is not None:
        error["details"] = details
    headers = {"WWW-Authenticate": "ApiKey"} if status_code == 401 else None
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


async def _handle_pulsegrid_error(request: Request, exc: Exception) -> Response:
    error = exc if isinstance(exc, PulseGridError) else PulseGridError(str(exc))
    status_code = status_for(error)
    if status_code >= 500:
        logger.error("request_failed", code=error.code, error=error.message)
    return error_response(request, status_code=status_code, code=error.code, message=error.message)


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    details: list[dict[str, object]] = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in errors
    ]
    return error_response(
        request,
        status_code=422,
        code="validation_failed",
        message="request validation failed",
        details=details,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    logger.error("unhandled_error", error_type=type(exc).__name__, exc_info=exc)
    return error_response(
        request, status_code=500, code="internal_error", message="internal server error"
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install every exception handler on ``app``."""
    app.add_exception_handler(PulseGridError, _handle_pulsegrid_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
