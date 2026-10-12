import json
from pathlib import Path

from commentator.events import EventBus, JsonlLogger


def test_publish_reaches_subscribers_and_jsonl(tmp_path: Path) -> None:
    received: list[dict] = []
    event_bus = EventBus()
    logger = JsonlLogger(tmp_path / "session" / "events.jsonl")
    event_bus.subscribe(received.append)
    event_bus.subscribe(logger)

    event_bus.publish("detection", "game_found", {"game": "pico_park"})
    logger.close()

    lines = (tmp_path / "session" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert received == [record]
    assert record["stage"] == "detection"
    assert record["type"] == "game_found"
    assert record["data"] == {"game": "pico_park"}
    assert isinstance(record["t"], float)
