import pytest

import main
from commentator.logs import read_log
from commentator.logs import session as session_mod


@pytest.fixture(autouse=True)
def no_dev_env(monkeypatch):
    monkeypatch.delenv(session_mod.DEV_ENV_VAR, raising=False)
    monkeypatch.delenv(session_mod.LOG_DIR_ENV_VAR, raising=False)


def test_normal_run_writes_no_logs(tmp_path, capsys):
    assert main.main(["--log-dir", str(tmp_path)]) == 0
    assert "ready" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_dev_flag_writes_a_session_log(tmp_path, capsys):
    assert main.main(["--dev", "--log-dir", str(tmp_path)]) == 0
    (session,) = tmp_path.iterdir()
    events = list(read_log(session))
    assert events[0].type == "session_started"
    assert events[0].data["argv"] == ["--dev", "--log-dir", str(tmp_path)]
    assert events[-1].type == "session_ended"
    assert str(session) in capsys.readouterr().out


def test_dev_env_var_works_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.setenv(session_mod.DEV_ENV_VAR, "1")
    main.main(["--log-dir", str(tmp_path)])
    assert len(list(tmp_path.iterdir())) == 1
