import threading

import pytest

from commentator.logs import EventBus, LogEvent


@pytest.fixture
def bus():
    b = EventBus()
    yield b
    b.close()


def collect(bus):
    received: list[LogEvent] = []
    bus.subscribe(received.append)
    return received


def test_publish_without_subscribers_is_a_no_op():
    b = EventBus()
    b.publish("capture", "frame_captured")
    assert b.flush()
    assert b.dropped == 0


def test_events_arrive_in_order_with_increasing_seq(bus):
    received = collect(bus)
    for i in range(100):
        bus.publish("director", "event_pushed", {"i": i})
    assert bus.flush()
    assert [e.data["i"] for e in received] == list(range(100))
    seqs = [e.seq for e in received]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all(e.stage == "director" and e.type == "event_pushed" for e in received)


def test_timestamps_are_non_decreasing(bus):
    received = collect(bus)
    for _ in range(50):
        bus.publish("vision", "result")
    bus.flush()
    times = [e.t for e in received]
    assert times == sorted(times)


def test_many_threads_publishing_lose_nothing(bus):
    received = collect(bus)
    threads_n, per_thread = 8, 500

    def worker(n):
        for i in range(per_thread):
            bus.publish(f"stage{n}", "tick", {"i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bus.flush()
    assert len(received) == threads_n * per_thread
    assert bus.dropped == 0
    for n in range(threads_n):
        mine = [e.data["i"] for e in received if e.stage == f"stage{n}"]
        assert mine == list(range(per_thread))


def test_full_buffer_drops_oldest_and_reports_it():
    bus = EventBus(max_buffer=10)
    gate = threading.Event()
    received: list[LogEvent] = []

    def slow(event):
        gate.wait(5)
        received.append(event)

    bus.subscribe(slow)
    bus.publish("test", "first")  # picked up by the bus thread, which then blocks on the gate
    deadline = 50
    while bus._buffer and deadline:  # wait until the bus thread has taken "first"
        threading.Event().wait(0.01)
        deadline -= 1
    for i in range(25):
        bus.publish("test", "burst", {"i": i})
    gate.set()
    assert bus.flush()
    bus.close()

    assert bus.dropped == 15
    bursts = [e.data["i"] for e in received if e.type == "burst"]
    assert bursts == list(range(15, 25))  # only the newest 10 survive
    dropped = [e for e in received if e.type == "events_dropped"]
    assert len(dropped) == 1
    assert dropped[0].stage == "logs" and dropped[0].level == "warning"
    assert dropped[0].data == {"count": 15, "total": 15}


def test_failing_subscriber_does_not_break_others(bus, capsys):
    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    received = collect(bus)
    for i in range(5):
        bus.publish("narrator", "line_written", {"i": i})
    assert bus.flush()
    assert len(received) == 5
    err = capsys.readouterr().err
    assert err.count("further errors suppressed") == 1  # reported once, not five times


def test_unsubscribe_stops_delivery(bus):
    received = collect(bus)
    bus.publish("a", "one")
    bus.flush()
    bus.unsubscribe(received.append)
    bus.publish("a", "two")
    bus.flush()
    assert [e.type for e in received] == ["one"]


def test_invalid_level_is_rejected(bus):
    collect(bus)
    with pytest.raises(ValueError):
        bus.publish("a", "b", level="fatal")


def test_close_delivers_buffered_events_and_ignores_later_publishes():
    bus = EventBus()
    received = collect(bus)
    for i in range(20):
        bus.publish("a", "b", {"i": i})
    bus.close()
    assert len(received) == 20
    bus.publish("a", "after_close")
    assert len(received) == 20


def test_to_dict_shape():
    event = LogEvent(seq=3, t=1.23456789, stage="director", type="event_dropped", data={"reason": "expired"})
    assert event.to_dict() == {
        "seq": 3,
        "t": 1.234568,
        "stage": "director",
        "type": "event_dropped",
        "level": "info",
        "data": {"reason": "expired"},
    }
