import json

import pytest
import structlog

from pulsegrid.core.logging import configure_logging, get_logger


def test_json_logging_emits_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_output=True)
    with structlog.contextvars.bound_contextvars(request_id="abc"):
        get_logger("test").info("hello", answer=42)
    get_logger("test").debug("filtered out")

    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "hello"
    assert record["answer"] == 42
    assert record["request_id"] == "abc"
    assert record["level"] == "info"
    assert record["logger_name"] == "test"
    assert record["timestamp"].endswith("Z")


def test_module_level_loggers_follow_later_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    early = get_logger("early")
    configure_logging("INFO", json_output=True)
    early.info("late_configured")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["logger_name"] == "early"


def test_console_logging(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG", json_output=False)
    get_logger("test").debug("console message")

    assert "console message" in capsys.readouterr().err
