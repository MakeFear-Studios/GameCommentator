import logging
import sys
import threading
from pathlib import Path

import pytest

from commentator.logs import EventBus, dev_session, read_log
from commentator.logs import session as session_mod


@pytest.fixture
def bus():
    b = EventBus()
    yield b
    b.close()


def types(session_dir):
    return [(e.stage, e.type) for e in read_log(session_dir)]


def test_records_start_and_end_with_info(bus, tmp_path):
    with dev_session(bus, root=tmp_path, session_info={"version": "1.2.3"}) as logger:
        bus.publish("capture", "frame_captured")
    events = list(read_log(logger.session_dir))
    assert [(e.stage, e.type) for e in events] == [
        ("logs", "session_started"),
        ("capture", "frame_captured"),
        ("logs", "session_ended"),
    ]
    assert events[0].data["version"] == "1.2.3"
    assert events[0].data["frozen"] is False


def test_crash_is_logged_and_reraised(bus, tmp_path):
    with pytest.raises(ZeroDivisionError):
        with dev_session(bus, root=tmp_path) as logger:
            1 / 0
    events = list(read_log(logger.session_dir))
    crash = next(e for e in events if e.type == "crashed")
    assert crash.stage == "app" and crash.level == "error"
    assert crash.data["error"].startswith("ZeroDivisionError")
    assert "1 / 0" in crash.data["traceback"]
    assert events[-1].type == "session_ended"  # still closed cleanly


def test_ctrl_c_is_logged_as_interrupted(bus, tmp_path):
    with pytest.raises(KeyboardInterrupt):
        with dev_session(bus, root=tmp_path) as logger:
            raise KeyboardInterrupt
    assert ("app", "interrupted") in types(logger.session_dir)
    assert ("app", "crashed") not in types(logger.session_dir)


# The crash is deliberate; our hook passes it on to pytest's own hook, which warns about it.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_background_thread_crash_is_logged(bus, tmp_path):
    def boom():
        raise RuntimeError("capture thread died")

    with dev_session(bus, root=tmp_path) as logger:
        t = threading.Thread(target=boom, name="capture")
        t.start()
        t.join()
    crash = next(e for e in read_log(logger.session_dir) if e.type == "thread_crashed")
    assert crash.data["thread"] == "capture"
    assert "capture thread died" in crash.data["error"]


def test_hooks_are_removed_afterwards(bus, tmp_path):
    hook_before = threading.excepthook
    handlers_before = list(logging.getLogger().handlers)
    with dev_session(bus, root=tmp_path):
        assert threading.excepthook is not hook_before
    assert threading.excepthook is hook_before
    assert logging.getLogger().handlers == handlers_before


def test_library_warnings_are_captured(bus, tmp_path):
    with dev_session(bus, root=tmp_path) as logger:
        logging.getLogger("ollama").warning("slow load")
    record = next(e for e in read_log(logger.session_dir) if e.stage == "log")
    assert record.data["message"] == "slow load"


# -- where logs go -----------------------------------------------------------


def test_default_root_from_source_is_repo_logs(monkeypatch):
    monkeypatch.delenv(session_mod.LOG_DIR_ENV_VAR, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    repo_root = Path(__file__).resolve().parents[2]
    assert session_mod.default_log_root() == repo_root / "logs"


def test_default_root_for_exe_is_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(session_mod.LOG_DIR_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert session_mod.default_log_root() == tmp_path / "GameCommentator" / "logs"


def test_env_var_overrides_log_root(monkeypatch, tmp_path):
    monkeypatch.setenv(session_mod.LOG_DIR_ENV_VAR, str(tmp_path / "custom"))
    assert session_mod.default_log_root() == tmp_path / "custom"


@pytest.mark.parametrize("value, expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False)])
def test_dev_mode_env_var(monkeypatch, value, expected):
    monkeypatch.setenv(session_mod.DEV_ENV_VAR, value)
    assert session_mod.dev_mode_from_env() is expected
