import logging

import pytest

from commentator.logs import EventBus, capture_python_logging


@pytest.fixture
def bus():
    b = EventBus()
    yield b
    b.close()


@pytest.fixture
def captured(bus):
    received = []
    bus.subscribe(received.append)
    handler = capture_python_logging(bus, level=logging.WARNING)
    yield received
    logging.getLogger().removeHandler(handler)


def test_warnings_from_libraries_reach_the_bus(bus, captured):
    logging.getLogger("ollama.client").warning("model %s not loaded", "qwen2.5vl:3b")
    bus.flush()
    (event,) = captured
    assert event.stage == "log" and event.type == "log_record"
    assert event.level == "warning"
    assert event.data == {"logger": "ollama.client", "message": "model qwen2.5vl:3b not loaded"}


def test_below_threshold_is_ignored(bus, captured):
    logging.getLogger("noisy").info("not interesting")
    bus.flush()
    assert captured == []


def test_exceptions_include_traceback_and_critical_maps_to_error(bus, captured):
    try:
        raise ValueError("bad frame")
    except ValueError:
        logging.getLogger("capture").critical("capture failed", exc_info=True)
    bus.flush()
    (event,) = captured
    assert event.level == "error"
    assert "ValueError: bad frame" in event.data["exception"]
