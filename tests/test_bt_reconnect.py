"""The Bluetooth reconnector: what it asks BlueZ for, and how often."""

from arctis_sound_manager import bt_reconnect as br


def test_mac_is_read_from_the_node_name():
    assert br.mac_of("bluez_output.30_96_10_49_54_E2.1") == "30:96:10:49:54:E2"
    assert br.mac_of("alsa_output.usb-SteelSeries-00.analog-stereo") is None
    assert br.mac_of("") is None


def _reconnector(monkeypatch, paired=True, connected=False):
    calls: list[list[str]] = []

    class _Res:
        stdout = "Connection successful\n"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "info":
            r = _Res()
            r.stdout = (f"Device {cmd[2]}\n\tPaired: {'yes' if paired else 'no'}\n"
                        f"\tConnected: {'yes' if connected else 'no'}\n")
            return r
        return _Res()

    monkeypatch.setattr(br.subprocess, "run", fake_run)

    # Run the worker inline so the test sees the connect synchronously.
    class _Thread:
        def __init__(self, target, args, **kw):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr(br.threading, "Thread", _Thread)
    return br.BluetoothReconnector(), calls


def test_missing_paired_device_is_asked_to_connect(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert ["bluetoothctl", "connect", "30:96:10:49:54:E2"] in calls


def test_attempts_back_off_and_reset_when_the_node_returns(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"game": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)
    r.tick(saved, lambda n: False, now=5.0)       # inside the first 15 s: nothing
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: False, now=16.0)      # second try, then 30 s
    assert sum(c[1] == "connect" for c in calls) == 2
    r.tick(saved, lambda n: False, now=40.0)
    assert sum(c[1] == "connect" for c in calls) == 2
    r.tick(saved, lambda n: True, now=41.0)       # back: backoff forgotten
    # Gone again well outside the "sent away on purpose" window (see below):
    # the first try is immediate, not the 60 s the backoff had reached.
    later = 41.0 + br.DELIBERATE_WINDOW_S + 1
    r.tick(saved, lambda n: False, now=later)
    assert sum(c[1] == "connect" for c in calls) == 3


def test_a_connected_or_unpaired_device_is_left_alone(monkeypatch):
    r, calls = _reconnector(monkeypatch, connected=True)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert not any(c[1] == "connect" for c in calls)
    r, calls = _reconnector(monkeypatch, paired=False)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert not any(c[1] == "connect" for c in calls)


def test_non_bluetooth_outputs_cost_nothing(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    probed = []
    r.tick({"game": "alsa_output.x"}, lambda n: probed.append(n) or True, now=0.0)
    assert not calls and not probed


def test_a_device_sent_away_after_a_reconnect_is_left_alone(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"chat": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)       # transport failed: reconnect
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: True, now=5.0)        # back
    r.tick(saved, lambda n: False, now=60.0)      # the user pressed Disconnect
    r.tick(saved, lambda n: False, now=120.0)
    r.tick(saved, lambda n: False, now=900.0)
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: True, now=1000.0)     # the user connected it again
    r.tick(saved, lambda n: False, now=2000.0)    # much later: a failure again
    assert sum(c[1] == "connect" for c in calls) == 2


def test_a_drop_long_after_a_reconnect_is_still_a_failure(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"chat": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)
    r.tick(saved, lambda n: True, now=5.0)
    r.tick(saved, lambda n: False, now=5.0 + br.DELIBERATE_WINDOW_S + 1)
    assert sum(c[1] == "connect" for c in calls) == 2


# ── a link that keeps dropping is rebuilt from scratch ────────────────────────

SAVED = {"game": "bluez_output.30_96_10_49_54_E2.1"}


def _flap(r, times, start=0.0, gap=60.0):
    """*times* drop-outs that BlueZ recovers from on its own, *gap* s apart."""
    t = start
    for _ in range(times):
        r.tick(SAVED, lambda n: True, now=t)
        r.tick(SAVED, lambda n: False, now=t + 5)
        t += gap
    return t


def _disconnects(calls):
    return sum(c[1] == "disconnect" for c in calls)


def test_three_drops_then_a_settled_return_rebuild_the_link(monkeypatch):
    """The afternoon this was written: four transport failures in six minutes,
    and the stream that came back was broken until a disconnect + connect."""
    r, calls = _reconnector(monkeypatch, connected=True)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    t = _flap(r, 3)
    r.tick(SAVED, lambda n: True, now=t)                    # back...
    assert _disconnects(calls) == 0                         # ...not settled yet
    r.tick(SAVED, lambda n: True, now=t + br.STABLE_BEFORE_REFRESH_S)
    assert _disconnects(calls) == 1
    assert calls[-1] == ["bluetoothctl", "connect", "30:96:10:49:54:E2"]


def test_two_drops_are_not_enough(monkeypatch):
    r, calls = _reconnector(monkeypatch, connected=True)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    t = _flap(r, 2)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 60)
    assert _disconnects(calls) == 0


def test_drops_spread_wider_than_the_window_do_not_add_up(monkeypatch):
    r, calls = _reconnector(monkeypatch, connected=True)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    t = _flap(r, 3, gap=br.FLAKY_WINDOW_S / 2 + 10)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 60)
    assert _disconnects(calls) == 0


def test_the_rebuild_is_not_repeated_inside_the_cooldown(monkeypatch):
    """A link failing for a reason this cannot fix must not be cut every few
    minutes on top of it."""
    r, calls = _reconnector(monkeypatch, connected=True)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    t = _flap(r, 3)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 30)
    assert _disconnects(calls) == 1
    t = _flap(r, 3, start=t + 60)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 30)
    assert _disconnects(calls) == 1
    t = _flap(r, 3, start=t + br.REFRESH_COOLDOWN_S)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 30)
    assert _disconnects(calls) == 2


def test_the_rebuild_itself_is_not_counted_as_a_drop(monkeypatch):
    """The node vanishes while it is being rebuilt; that is ours, and must not
    start the count towards the next rebuild or read as the user sending it
    away."""
    r, calls = _reconnector(monkeypatch, connected=True)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    t = _flap(r, 3)
    r.tick(SAVED, lambda n: True, now=t)
    r.tick(SAVED, lambda n: True, now=t + 30)
    assert r._drops["30:96:10:49:54:E2"] == []
    assert "30:96:10:49:54:E2" not in r._dismissed


def test_a_refused_first_connect_is_retried(monkeypatch):
    """Straight after a disconnect the first connect can be refused
    (br-connection-unknown) and the next one succeed."""
    answers = iter(["Failed to connect: org.bluez.Error.Failed br-connection-unknown",
                    "Connection successful"])
    calls = []

    class _Res:
        def __init__(self, out):
            self.stdout, self.stderr = out, ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _Res(next(answers) if cmd[1] == "connect" else "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    monkeypatch.setattr(br.time, "sleep", lambda s: None)
    assert br.refresh_connection("30:96:10:49:54:E2") is True
    assert [c[1] for c in calls] == ["disconnect", "connect", "connect"]
