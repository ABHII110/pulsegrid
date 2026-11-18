"""Exception hierarchy shared by every layer.

Interfaces translate these into HTTP responses (``api.errors``) and CLI exit codes
(``cli.main``); nothing below the interface layer raises framework-specific errors.
"""

from typing import ClassVar
from uuid import UUID


class PulseGridError(Exception):
    """Base class for every error raised deliberately by PulseGrid.

    Attributes:
        code: Stable machine-readable identifier exposed to API and CLI clients.
        message: Human-readable description.
    """

    code: ClassVar[str] = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(PulseGridError):
    """The process is missing or has invalid configuration."""

    code = "configuration_error"


class AuthenticationError(PulseGridError):
    """The caller did not present valid credentials."""

    code = "unauthorized"


class DomainError(PulseGridError):
    """A business rule rejected the requested operation."""

    code = "domain_error"


class ValidationFailedError(DomainError):
    """Input passed schema validation but violates a business rule."""

    code = "validation_failed"


class NotFoundError(DomainError):
    """A requested entity does not exist."""

    code = "not_found"


class MonitorNotFoundError(NotFoundError):
    """No monitor exists with the given identifier."""

    def __init__(self, monitor_id: UUID) -> None:
        super().__init__(f"monitor {monitor_id} not found")
        self.monitor_id = monitor_id


class ConflictError(DomainError):
    """The operation conflicts with the current state."""

    code = "conflict"


class DuplicateMonitorError(ConflictError):
    """A monitor with the same name already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a monitor named {name!r} already exists")
        self.name = name


class CheckInProgressError(ConflictError):
    """Another process is currently checking the monitor."""

    def __init__(self, monitor_id: UUID) -> None:
        super().__init__(f"a check for monitor {monitor_id} is already running")
        self.monitor_id = monitor_id


class InfrastructureError(PulseGridError):
    """An external dependency (database, cache, network) failed."""

    code = "service_unavailable"


class RepositoryError(InfrastructureError):
    """The persistence layer failed."""


class NotificationError(InfrastructureError):
    """An incident notification could not be delivered."""
