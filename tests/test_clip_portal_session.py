# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every screencast session ASM opens has to be closed on the bus.

Reported from use as three record symbols stacked in the corner of the screen,
overlapping. They were three live portal sessions. The portal keeps a session
until its client calls Close or drops off the bus, and the GUI does neither
when a capture stops — it goes on running — so `self.portal = None` released
the Python reference and left the session exactly where it was. The compositor
draws one recording indicator per live session, so a Stop/Start cycle, a
pipeline restart, or a capture that failed to start each added one more.

The object is built with `__new__` and given a recording stand-in for the bus:
`close()` touches only `bus`, `session`, `_Gio` and `_closed_sub`, and going
through `__init__` would mean a live D-Bus connection and PyGObject just to
observe one method call. Only the two calls verified against Gio are stood in
for — `call_sync` and `signal_unsubscribe`.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.clip_capture import ClipCapture, ScreenCastPortal

PORTAL = "org.freedesktop.portal.Desktop"
SESSION_IFACE = "org.freedesktop.portal.Session"


class _RecordingBus:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple] = []
        self.unsubscribed: list[int] = []
        self._fail = fail

    def call_sync(self, dest, path, iface, method, *rest):
        self.calls.append((dest, path, iface, method))
        if self._fail:
            raise RuntimeError("the portal is not answering")

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)


class _Flags:
    class DBusCallFlags:
        NONE = 0

    class DBusSignalFlags:
        NONE = 0


def _portal(bus: _RecordingBus, session: str | None = "/session/1",
            sub: int | None = 7) -> ScreenCastPortal:
    p = object.__new__(ScreenCastPortal)
    p.bus = bus
    p.session = session
    p.closed = False
    p._closed_sub = sub
    p._Gio = _Flags
    return p


class _FakePortal:
    def __init__(self):
        self.closed_calls = 0

    def close(self):
        self.closed_calls += 1


# ── closing ───────────────────────────────────────────────────────────────────


def test_close_calls_the_portal_and_not_just_the_garbage_collector():
    bus = _RecordingBus()
    portal = _portal(bus)

    portal.close()

    assert bus.calls == [(PORTAL, "/session/1", SESSION_IFACE, "Close")]
    assert portal.session is None


def test_close_drops_the_signal_subscription():
    """The Closed subscription outlives the session it was made for, and its
    callback holds the portal object alive with it."""
    bus = _RecordingBus()
    portal = _portal(bus, sub=7)

    portal.close()

    assert bus.unsubscribed == [7]
    assert portal._closed_sub is None


def test_close_on_a_portal_that_never_opened_does_nothing():
    bus = _RecordingBus()
    portal = _portal(bus, session=None)

    portal.close()

    assert bus.calls == []


def test_close_twice_only_closes_once():
    bus = _RecordingBus()
    portal = _portal(bus)

    portal.close()
    portal.close()

    assert len(bus.calls) == 1


def test_a_portal_that_refuses_to_close_does_not_raise():
    """This runs on the way out of a capture. A session that cannot be closed
    is not a reason to fail the stop the user asked for."""
    portal = _portal(_RecordingBus(fail=True))

    portal.close()  # must not raise

    assert portal.session is None


# ── the call sites ────────────────────────────────────────────────────────────


def _capture_shell(portal) -> ClipCapture:
    """A ClipCapture with only what stop()/restart() reach for."""
    cap = object.__new__(ClipCapture)
    cap.pipeline = None
    cap.portal = portal
    return cap


def test_stop_closes_the_session():
    portal = _FakePortal()
    cap = _capture_shell(portal)

    cap.stop()

    assert portal.closed_calls == 1
    assert cap.portal is None


def test_stop_without_a_capture_is_harmless():
    cap = _capture_shell(None)

    cap.stop()  # must not raise

    assert cap.portal is None


def test_restart_closes_the_old_session_before_opening_the_next(monkeypatch):
    """restart() deliberately keeps the user out of the picker by reopening
    from the saved token — but the session it is replacing still has to go, or
    the indicator it draws stays on screen for a pipeline that no longer
    exists."""
    class _Clearable:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    portal = _FakePortal()
    cap = _capture_shell(portal)
    cap._Gst = None            # pipeline is None, so Gst is never reached
    cap.buffer = _Clearable()
    cap.caps = _Clearable()
    cap._pts_offset = _Clearable()

    order: list[str] = []
    monkeypatch.setattr(portal, "close",
                        lambda: order.append("closed"))
    monkeypatch.setattr(ClipCapture, "start",
                        lambda self: order.append("started"))

    cap.restart()

    assert order == ["closed", "started"], order


def test_starting_twice_does_not_strand_the_first_session(monkeypatch):
    """Overwriting self.portal would leave a session on the bus with nothing
    left holding a handle able to close it."""
    first = _FakePortal()
    cap = _capture_shell(first)

    # Fail the second open immediately: everything after it in start() needs a
    # live pipeline, and the question here is only what happened to `first`.
    monkeypatch.setattr(
        "arctis_sound_manager.clip_capture.ScreenCastPortal",
        lambda: (_ for _ in ()).throw(RuntimeError("no portal")))
    cap._Gst = None

    with pytest.raises(RuntimeError):
        cap.start()

    assert first.closed_calls == 1


def test_a_failed_open_closes_the_half_made_session(monkeypatch):
    """CreateSession may have succeeded before the picker was cancelled. That
    half-open session is as visible to the compositor as a working one."""
    made = _FakePortal()

    def _open(window=False):
        raise RuntimeError("cancelled")

    made.open = _open
    monkeypatch.setattr(
        "arctis_sound_manager.clip_capture.ScreenCastPortal", lambda: made)

    cap = _capture_shell(None)
    cap._Gst = None
    cap.window = False

    with pytest.raises(RuntimeError):
        cap.start()

    assert made.closed_calls == 1
    assert cap.portal is None


# ── what the saved choice is allowed to restore ────────────────────────────────

def _token_file(monkeypatch, tmp_path, payload):
    import json

    from arctis_sound_manager import clip_capture

    path = tmp_path / "clip_screencast_token.json"
    if payload is not None:
        path.write_text(json.dumps(payload))
    monkeypatch.setattr(clip_capture, "TOKEN_FILE", path)
    monkeypatch.setattr(clip_capture, "CONFIG_DIR", tmp_path)
    return path


def test_a_window_token_is_not_restored_for_a_screen(monkeypatch, tmp_path):
    """The reported bug, in one line: a token restores the exact source it was
    made for, so a window saved when windows were on offer kept coming back as
    a clip of a file manager labelled with the game the audio came from."""
    _token_file(monkeypatch, tmp_path, {"restore_token": "abc", "kind": "window"})

    assert ScreenCastPortal._load_token("monitor") is None


def test_a_token_from_before_the_kind_was_recorded_is_ignored(monkeypatch, tmp_path):
    """Written by a version that offered screens and windows together, so what
    it points at is unknown — and an unknown source is the one that cannot be
    corrected without asking."""
    _token_file(monkeypatch, tmp_path, {"restore_token": "abc"})

    assert ScreenCastPortal._load_token("monitor") is None


def test_a_screen_token_is_restored(monkeypatch, tmp_path):
    _token_file(monkeypatch, tmp_path, {"restore_token": "abc", "kind": "monitor"})

    assert ScreenCastPortal._load_token("monitor") == "abc"


def test_saving_records_which_kind_it_was(monkeypatch, tmp_path):
    import json

    path = _token_file(monkeypatch, tmp_path, None)

    ScreenCastPortal._save_token("abc", "monitor")

    assert json.loads(path.read_text()) == {"restore_token": "abc",
                                            "kind": "monitor"}


def test_has_saved_source_follows_the_same_rule(monkeypatch, tmp_path):
    from arctis_sound_manager.clip_capture import has_saved_source

    _token_file(monkeypatch, tmp_path, {"restore_token": "abc", "kind": "window"})
    assert has_saved_source("monitor") is False

    _token_file(monkeypatch, tmp_path, {"restore_token": "abc", "kind": "monitor"})
    assert has_saved_source("monitor") is True


# ── what the picker is allowed to offer ────────────────────────────────────────

class _FakeVariant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value


class _FakeGLib:
    Variant = _FakeVariant

    class VariantType:
        def __init__(self, signature):
            self.signature = signature


class _OpeningBus(_RecordingBus):
    """Enough of Gio for open() to run: a subscription and the fd handshake."""

    def signal_subscribe(self, *_args, **_kwargs):
        return 7

    def call_with_unix_fd_list_sync(self, *_args, **_kwargs):
        class _Reply:
            @staticmethod
            def unpack():
                return (0,)

        class _Fds:
            @staticmethod
            def get(_handle):
                return 99

        return _Reply(), _Fds()


def _opened(monkeypatch, tmp_path, kind_arg):
    """Run open() against fakes and hand back the options it asked for."""
    _token_file(monkeypatch, tmp_path, None)
    portal = _portal(_OpeningBus(), session=None, sub=None)
    portal._GLib, portal._Gio = _FakeGLib, _Flags
    seen: dict = {}

    def _call(method, _signature, pre_args, options):
        if method == "CreateSession":
            return {"session_handle": "/session/1"}
        if method == "SelectSources":
            seen.update(options)
            return {}
        if method == "Start":
            return {"restore_token": "tok", "streams": [(42, {})]}
        return {}

    portal._call = _call
    portal.open(**kind_arg)
    return seen


def test_the_picker_offers_screens_only(monkeypatch, tmp_path):
    """1 is MONITOR. Offering 1|2 let a window be chosen, and the clip that
    came out was a file manager — recorded faithfully, for weeks, because the
    choice was then restored every time."""
    asked = _opened(monkeypatch, tmp_path, {})

    assert asked["types"].value == 1


def test_asking_for_a_window_still_asks_for_a_window(monkeypatch, tmp_path):
    asked = _opened(monkeypatch, tmp_path, {"window": True})

    assert asked["types"].value == 2


def test_the_token_is_saved_under_the_kind_that_was_asked_for(monkeypatch, tmp_path):
    import json

    from arctis_sound_manager import clip_capture

    _opened(monkeypatch, tmp_path, {})

    assert json.loads(clip_capture.TOKEN_FILE.read_text())["kind"] == "monitor"
