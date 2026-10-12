import dataclasses
import enum
import json
from pathlib import Path

import numpy as np
import pytest

from commentator.logs import FORMAT_VERSION, DevLogger, EventBus, read_log


@pytest.fixture
def bus():
    b = EventBus()
    yield b
    b.close()


def lines(session_dir: Path) -> list[dict]:
    text = (session_dir / "events.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_writes_session_folder_with_start_and_end_events(bus, tmp_path):
    with DevLogger(bus, root=tmp_path, session_info={"game": "elden_ring"}) as logger:
        bus.publish("capture", "frame_captured", {"w": 1920, "h": 1080})
    session = logger.session_dir

    assert session.parent == tmp_path
    events = lines(session)
    assert [e["type"] for e in events] == ["session_started", "frame_captured", "session_ended"]
    started = events[0]
    assert started["stage"] == "logs"
    assert started["data"]["format_version"] == FORMAT_VERSION
    assert started["data"]["game"] == "elden_ring"
    assert events[1]["data"] == {"w": 1920, "h": 1080}
    assert set(events[1]) == {"seq", "t", "stage", "type", "level", "data"}

    info = json.loads((session / "session.json").read_text(encoding="utf-8"))
    assert info["format_version"] == FORMAT_VERSION
    assert info["started_at"] and info["ended_at"]
    assert info["events_written"] == 3
    assert info["events_dropped"] == 0
    assert info["app"] == {"game": "elden_ring"}
    assert (session / "frames").is_dir()


def test_events_reach_disk_before_stop(bus, tmp_path):
    logger = DevLogger(bus, root=tmp_path)
    session = logger.start()
    bus.publish("vision", "result", {"ok": True})
    bus.flush()
    # Line-buffered: readable while the session is still running (survives a crash).
    assert [e["type"] for e in lines(session)] == ["session_started", "result"]
    logger.stop()


def test_stop_unsubscribes_so_later_events_are_not_written(bus, tmp_path):
    with DevLogger(bus, root=tmp_path) as logger:
        pass
    bus.publish("vision", "after_stop")
    bus.flush()
    assert [e["type"] for e in lines(logger.session_dir)] == ["session_started", "session_ended"]


def test_two_sessions_in_the_same_second_get_separate_folders(bus, tmp_path):
    with DevLogger(bus, root=tmp_path) as first:
        pass
    with DevLogger(bus, root=tmp_path) as second:
        pass
    assert first.session_dir != second.session_dir
    assert len(list(tmp_path.iterdir())) == 2


class Priority(enum.StrEnum):
    HIGH = "high"


@dataclasses.dataclass
class Point:
    x: int
    y: int


def test_awkward_data_is_serialised_not_crashed_on(bus, tmp_path):
    with DevLogger(bus, root=tmp_path) as logger:
        bus.publish(
            "vision",
            "result",
            {
                "priority": Priority.HIGH,
                "point": Point(1, 2),
                "frame": np.zeros((720, 1280, 3), dtype=np.uint8),
                "score": np.float32(0.5),
                "path": Path("frames/000001.png"),
                "png": b"\x89PNG....",
                "tags": {"b", "a"},
                "other": object(),
            },
        )
    data = lines(logger.session_dir)[1]["data"]
    assert data["priority"] == "high"
    assert data["point"] == {"x": 1, "y": 2}
    assert data["frame"] == {"array": {"shape": [720, 1280, 3], "dtype": "uint8"}}
    assert data["score"] == 0.5
    assert data["path"] == str(Path("frames/000001.png"))
    assert data["png"] == {"bytes": 8}
    assert data["tags"] == ["a", "b"]
    assert data["other"].startswith("<object object")


def test_save_frame_writes_png_and_returns_relative_path(bus, tmp_path):
    with DevLogger(bus, root=tmp_path) as logger:
        rel = logger.save_frame(b"\x89PNGfake")
        bus.publish("capture", "frame_sampled", {"frame": rel})
    assert rel == "frames/000001.png"
    assert (logger.session_dir / rel).read_bytes() == b"\x89PNGfake"


def test_read_log_round_trips(bus, tmp_path):
    with DevLogger(bus, root=tmp_path) as logger:
        for i in range(5):
            bus.publish("director", "event_pushed", {"i": i}, level="debug")
    events = list(read_log(logger.session_dir))
    assert [e.type for e in events] == ["session_started"] + ["event_pushed"] * 5 + ["session_ended"]
    pushed = [e for e in events if e.type == "event_pushed"]
    assert [e.data["i"] for e in pushed] == list(range(5))
    assert all(e.level == "debug" and e.stage == "director" for e in pushed)
    assert [e.seq for e in events] == sorted(e.seq for e in events)
