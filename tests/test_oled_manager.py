# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the OLED USB circuit breaker (issue #100).

Context: `_send_oled_packet` retries 5x per HID packet, and a single frame
is made of several packets. When the OLED interface stays continuously
EBUSY (e.g. a container/distrobox holding the USB handle, or a stray
process), the periodic refresh loop used to flood the log with one
"OLED USB error after 5 attempts: [Errno 16] Resource busy" warning per
packet, forever.

These tests exercise `_send_current_frame` / `_send_oled_packet` directly
against a fake USB device, bypassing the real renderer/protocol pipeline
(only the *number* of packets in a frame and whether each is accepted
matters for the breaker logic).
"""

from __future__ import annotations

import errno as errno_mod
import logging
import threading
import time as time_mod
from types import SimpleNamespace

import usb.core

from arctis_sound_manager.oled_manager import (
    OledManager,
    _OLED_BUSY_FAIL_THRESHOLD,
)

_LOGGER_NAME = "arctis_sound_manager.oled_manager"


class _FakeUsbDevice:
    """Stand-in for the pyusb Device: ctrl_transfer either succeeds or
    always raises USBError with a configurable errno."""

    def __init__(self, fail_errno: int | None = None) -> None:
        self.fail_errno = fail_errno
        self.call_count = 0

    def ctrl_transfer(self, *args, **kwargs):
        self.call_count += 1
        if self.fail_errno is not None:
            raise usb.core.USBError("busy", errno=self.fail_errno)
        return None


class _FakeCore:
    """Minimal CoreEngine stand-in — only what OledManager.__init__ and
    _send_current_frame/_send_oled_packet touch."""

    def __init__(self, usb_device: _FakeUsbDevice | None) -> None:
        self.usb_device = usb_device
        self._usb_write_lock = threading.Lock()
        self.device_config = None
        self.general_settings = SimpleNamespace(oled_brightness=50)


def _make_manager(usb_device, monkeypatch, packet_count: int = 2) -> OledManager:
    core = _FakeCore(usb_device)
    manager = OledManager(core)
    # The breaker only cares about "how many packets does this frame have"
    # and "did each ctrl_transfer succeed" — stub out rendering entirely.
    monkeypatch.setattr(
        manager._protocol, "build_frame_packets",
        lambda *a, **k: [[0, 0, 0, 0] for _ in range(packet_count)],
    )
    monkeypatch.setattr(manager._renderer, "crop_frame", lambda *a, **k: b"\x00")
    manager._current_image = object()  # just needs to be non-None
    monkeypatch.setattr(time_mod, "sleep", lambda *_: None)  # skip retry backoff
    return manager


# ---------------------------------------------------------------------------
# refresh(): live redraw on a settings change, gated on Custom Display (#172)
# ---------------------------------------------------------------------------

def test_refresh_redraws_only_when_custom_display_enabled(monkeypatch):
    """A settings change redraws the custom display live — but only when Custom
    Display is on; otherwise the DAC's own UI must stay put (#172)."""
    manager = _make_manager(_FakeUsbDevice(None), monkeypatch)
    calls = {"update": 0, "reset": 0}
    monkeypatch.setattr(manager, "update_display",
                        lambda activity=True: calls.__setitem__("update", calls["update"] + 1))
    monkeypatch.setattr(manager, "_reset_scroll",
                        lambda: calls.__setitem__("reset", calls["reset"] + 1))

    # Custom Display OFF → no redraw (must not take the screen back over).
    manager._core.general_settings.oled_custom_display = False
    manager.refresh()
    assert calls == {"update": 0, "reset": 0}

    # Custom Display ON → redraw from the current settings.
    manager._core.general_settings.oled_custom_display = True
    manager.refresh()
    assert calls == {"update": 1, "reset": 1}


# ---------------------------------------------------------------------------
# _show_splash(): never steals the screen when Custom Display is off
# ---------------------------------------------------------------------------

def test_splash_skipped_when_custom_display_disabled(monkeypatch):
    """With Custom Display off the DAC's own UI must stay on screen.

    The splash is not just cosmetic here: it sets `_splash_until`, and the
    refresh loop sleeps until that expires, so nothing sends the return-to-UI
    packet meanwhile — the DAC would sit on the ASM logo for the full splash
    duration every time the daemon starts, the GUI opens or the tray icon is
    clicked (reported on Discord for a Nova Pro Wired DAC).
    """
    manager = _make_manager(_FakeUsbDevice(None), monkeypatch)
    sent: list[list[int]] = []
    monkeypatch.setattr(manager, "_send_oled_packet",
                        lambda packet, control=False: sent.append(packet) or True)
    monkeypatch.setattr(manager._renderer, "render_splash_image", lambda: b"\x00")

    manager._core.general_settings.oled_custom_display = False
    manager._show_splash()
    assert sent == []
    # The refresh loop must not be parked either — it is what hands the panel
    # back to the firmware.
    assert manager._splash_until == 0.0

    manager._core.general_settings.oled_custom_display = True
    manager._show_splash()
    assert sent, "splash must still be drawn when Custom Display is on"
    assert manager._splash_until > 0.0


# ---------------------------------------------------------------------------
# _send_oled_packet: bool return contract
# ---------------------------------------------------------------------------

def test_send_oled_packet_returns_true_on_success(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=None)
    manager = _make_manager(dev, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is True


def test_send_oled_packet_returns_false_after_retries_exhausted(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is False
    assert manager._last_send_errno == errno_mod.EBUSY
    assert dev.call_count == 5  # _MAX_ATTEMPTS


def test_send_oled_packet_returns_false_when_device_gone(monkeypatch):
    manager = _make_manager(None, monkeypatch, packet_count=1)
    assert manager._send_oled_packet([1, 2, 3]) is False
    assert manager._last_send_errno is None


# ---------------------------------------------------------------------------
# Circuit breaker: trips after N consecutive EBUSY frames, single warning
# ---------------------------------------------------------------------------

def test_circuit_breaker_trips_after_threshold_with_single_warning(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
            manager._send_current_frame()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "suspended for 60s" in warnings[0].getMessage()
    assert manager._suspend_until > 0
    assert manager._frame_fail_streak == 0  # counter reset once tripped


def test_circuit_breaker_does_not_trip_before_threshold(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(_OLED_BUSY_FAIL_THRESHOLD - 1):
            manager._send_current_frame()

    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert manager._suspend_until == 0.0
    assert manager._frame_fail_streak == _OLED_BUSY_FAIL_THRESHOLD - 1


def test_circuit_breaker_ignores_non_ebusy_errors(monkeypatch, caplog):
    """A different USB error (e.g. EPIPE) is not the distrobox-EBUSY-spam
    scenario this breaker targets — never counted, never suspended."""
    dev = _FakeUsbDevice(fail_errno=errno_mod.EPIPE)
    manager = _make_manager(dev, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        for _ in range(5):
            manager._send_current_frame()

    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert manager._frame_fail_streak == 0
    assert manager._suspend_until == 0.0


# ---------------------------------------------------------------------------
# Circuit breaker: suspension actually silences the device / logs
# ---------------------------------------------------------------------------

def test_suspended_frame_send_does_not_touch_device(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()
    assert manager._suspend_until > 0

    calls_before = dev.call_count
    manager._send_current_frame()
    assert dev.call_count == calls_before  # early-returned, no ctrl_transfer


def test_suspended_frame_send_logs_only_debug(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        manager._send_current_frame()

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Reset semantics: success, and device re-attach
# ---------------------------------------------------------------------------

def test_frame_fail_streak_resets_on_success(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    manager._send_current_frame()
    manager._send_current_frame()
    assert manager._frame_fail_streak == 2

    dev.fail_errno = None  # device recovered
    manager._send_current_frame()
    assert manager._frame_fail_streak == 0
    assert manager._suspend_until == 0.0


def test_resumes_after_suspend_window_elapses(monkeypatch):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    for _ in range(_OLED_BUSY_FAIL_THRESHOLD):
        manager._send_current_frame()
    assert manager._suspend_until > 0

    # Simulate the 60s suspend window having elapsed.
    manager._suspend_until = 0.0
    dev.fail_errno = None
    calls_before = dev.call_count
    manager._send_current_frame()
    assert dev.call_count > calls_before  # attempted again
    assert manager._frame_fail_streak == 0


def test_breaker_resets_on_device_reattach(monkeypatch, caplog):
    dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager = _make_manager(dev, monkeypatch)
    manager._send_current_frame()
    manager._send_current_frame()
    assert manager._frame_fail_streak == 2

    # A re-attach swaps in a new USB device object (still busy).
    new_dev = _FakeUsbDevice(fail_errno=errno_mod.EBUSY)
    manager._core.usb_device = new_dev

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        manager._send_current_frame()

    # The streak must restart at 1 (reset-then-increment), not 3 — so no
    # warning should fire yet even though this is nominally the 3rd call.
    assert manager._frame_fail_streak == 1
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
