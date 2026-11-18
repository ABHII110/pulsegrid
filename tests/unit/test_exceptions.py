from uuid import uuid4

import pytest

from pulsegrid.core.exceptions import (
    AuthenticationError,
    CheckInProgressError,
    ConfigurationError,
    ConflictError,
    DomainError,
    DuplicateMonitorError,
    InfrastructureError,
    MonitorNotFoundError,
    NotFoundError,
    NotificationError,
    PulseGridError,
    RepositoryError,
    ValidationFailedError,
)


def test_monitor_not_found_carries_id() -> None:
    monitor_id = uuid4()
    error = MonitorNotFoundError(monitor_id)

    assert error.monitor_id == monitor_id
    assert str(monitor_id) in error.message
    assert isinstance(error, NotFoundError)
    assert error.code == "not_found"


def test_duplicate_monitor_is_conflict() -> None:
    error = DuplicateMonitorError("api")

    assert isinstance(error, ConflictError)
    assert error.code == "conflict"
    assert "'api'" in str(error)


def test_check_in_progress_is_conflict() -> None:
    assert isinstance(CheckInProgressError(uuid4()), ConflictError)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigurationError("x"), "configuration_error"),
        (AuthenticationError("x"), "unauthorized"),
        (ValidationFailedError("x"), "validation_failed"),
        (RepositoryError("x"), "service_unavailable"),
        (NotificationError("x"), "service_unavailable"),
        (PulseGridError("x"), "internal_error"),
    ],
)
def test_codes(error: PulseGridError, code: str) -> None:
    assert error.code == code


def test_hierarchy() -> None:
    assert issubclass(ValidationFailedError, DomainError)
    assert issubclass(RepositoryError, InfrastructureError)
    assert issubclass(InfrastructureError, PulseGridError)
