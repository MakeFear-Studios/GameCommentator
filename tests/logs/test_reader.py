import pytest

from commentator.logs import read_log

GOOD = '{"seq": 1, "t": 0.5, "stage": "vision", "type": "result", "level": "info", "data": {"ok": true}}'


def test_accepts_file_or_session_folder(tmp_path):
    (tmp_path / "events.jsonl").write_text(GOOD + "\n", encoding="utf-8")
    from_file = list(read_log(tmp_path / "events.jsonl"))
    from_dir = list(read_log(tmp_path))
    assert from_file == from_dir
    assert from_file[0].stage == "vision" and from_file[0].data == {"ok": True}


def test_skips_blank_and_half_written_lines(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text(GOOD + "\n\n" + '{"seq": 2, "t": 0.6, "sta', encoding="utf-8")
    events = list(read_log(log))
    assert [e.seq for e in events] == [1]


def test_strict_mode_raises_on_bad_lines(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text(GOOD + "\nnot json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="events.jsonl:2"):
        list(read_log(log, strict=True))


def test_missing_optional_fields_get_defaults(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text('{"seq": 1, "t": 0, "stage": "a", "type": "b"}\n', encoding="utf-8")
    (event,) = read_log(log)
    assert event.level == "info" and event.data == {}
