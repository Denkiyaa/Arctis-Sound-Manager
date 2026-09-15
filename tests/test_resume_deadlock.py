# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The 1.4.25 wake-from-suspend deadlock (#238).

A py-spy dump of a hung daemon showed three parties:

  * event-loop thread: resume_from_sleep -> configure_virtual_sinks, blocked
    in _detect_lock.acquire();
  * pyudev thread: on_device_connected -> configure_virtual_sinks, holding
    _detect_lock, blocked on `with self._device_lock`;
  * the listen loop coroutine, parked in `await asyncio.sleep()` *inside*
    `with self._device_lock`, so the loop thread owned the RLock and could
    not release it while it was itself blocked.

These tests pin the two halves of the fix: the listen loop must not hold the
device lock across its idle await, and a coroutine must never call
configure_virtual_sinks() synchronously on the event loop.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

from arctis_sound_manager import core as core_mod
from arctis_sound_manager.core import CoreEngine


def _lock_is_free(lock: threading.RLock, timeout: float = 0.5) -> bool:
    """Whether another thread can take *lock* right now."""
    got = []

    def _try() -> None:
        if lock.acquire(timeout=timeout):
            lock.release()
            got.append(True)

    t = threading.Thread(target=_try)
    t.start()
    t.join()
    return bool(got)


def test_listen_loop_idle_backoff_does_not_hold_the_device_lock(monkeypatch):
    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.RLock()
    engine.usb_device = None  # the state prepare_for_sleep() leaves behind

    observed: list[bool] = []

    async def fake_sleep(_seconds: float) -> None:
        observed.append(_lock_is_free(engine._device_lock))

    monkeypatch.setattr(core_mod.asyncio, "sleep", fake_sleep)

    asyncio.run(CoreEngine.listen_endpoint_loop(engine, interface_id=3))

    assert observed == [True], (
        "listen_endpoint_loop awaited its idle back-off while holding "
        "_device_lock - any other thread needing the lock now waits on the "
        "event loop, and deadlocks if the loop thread blocks")


def _resume_engine() -> CoreEngine:
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._detect_lock = threading.Lock()
    engine._device_configured_event = threading.Event()
    engine._device_configured_event.set()
    engine.usb_device = None  # released by prepare_for_sleep()
    engine.device_config = None
    engine._resume_reset_attempted = False
    engine._last_usb_reset_monotonic = 0.0
    return engine


def test_resume_does_not_block_the_event_loop_on_a_busy_detection_lock(monkeypatch):
    """The udev thread already holds _detect_lock when the resume task runs.

    The loop must stay responsive: a timer scheduled on it has to fire while
    the other thread is still holding the lock. With the synchronous call it
    never fired - the loop thread sat in _detect_lock.acquire().
    """
    monkeypatch.setattr(core_mod, "_RESUME_CONFIGURE_WAIT_TIMEOUT_S", 0.2)
    engine = _resume_engine()
    engine.configure_virtual_sinks = MagicMock(
        side_effect=lambda: (engine._detect_lock.acquire(), engine._detect_lock.release()))

    release = threading.Event()
    alive_at: list[float] = []

    def udev_thread() -> None:
        with engine._detect_lock:
            release.wait(3.0)  # hold the lock until the loop proves it is alive

    holder = threading.Thread(target=udev_thread, daemon=True)
    holder.start()
    while not engine._detect_lock.locked():
        pass
    t0 = time.monotonic()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, lambda: alive_at.append(time.monotonic() - t0))
        loop.call_later(0.1, release.set)  # only reached while the loop is not blocked
        await CoreEngine.resume_from_sleep(engine)
        await asyncio.sleep(0.2)  # let both timers fire

    asyncio.run(scenario())
    holder.join(1.0)

    # With the synchronous call the loop thread sat in _detect_lock.acquire()
    # until the holder gave up after 3 s, and the timer fired only then.
    assert alive_at and alive_at[0] < 1.0, (
        f"the event loop was blocked while another thread held _detect_lock "
        f"(first timer fired after {alive_at[0] if alive_at else 'never'}s)")
    # A detection already in flight is left to finish; none is queued behind it.
    engine.configure_virtual_sinks.assert_not_called()


def test_resume_runs_detection_off_the_loop_when_nothing_is_in_flight(monkeypatch):
    monkeypatch.setattr(core_mod, "_RESUME_CONFIGURE_WAIT_TIMEOUT_S", 0.2)
    engine = _resume_engine()
    ran_on: list[str] = []
    engine.configure_virtual_sinks = MagicMock(
        side_effect=lambda: ran_on.append(threading.current_thread().name))

    async def scenario() -> str:
        await asyncio.wait_for(CoreEngine.resume_from_sleep(engine), timeout=2.0)
        return threading.current_thread().name

    loop_thread = asyncio.run(scenario())

    engine.configure_virtual_sinks.assert_called_once()
    assert ran_on and ran_on[0] != loop_thread, (
        "configure_virtual_sinks() ran on the event-loop thread; its blocking "
        "_detect_lock.acquire() stalls every coroutine, including the one "
        "holding _device_lock")


def test_usb_reset_escalation_also_defers_to_an_in_flight_detection(monkeypatch):
    monkeypatch.setattr(core_mod, "_USB_RESET_SETTLE_S", 0)
    monkeypatch.setattr(core_mod, "reset_via_usbfs", lambda dev, log: True)
    monkeypatch.setattr(core_mod.usb.util, "dispose_resources", lambda dev: None)
    engine = _resume_engine()
    engine.usb_device = MagicMock(idProduct=0x2290, bus=1, port_numbers=(6,))
    engine.device_config = MagicMock(vendor_id=0x1038)
    engine._record_usb_reset_attempt = MagicMock()
    engine.configure_virtual_sinks = MagicMock()
    engine._detect_lock.acquire()  # a detection is in flight on another thread
    try:
        asyncio.run(asyncio.wait_for(
            CoreEngine._escalate_to_usb_reset(engine, reason="test"), timeout=2.0))
    finally:
        engine._detect_lock.release()

    engine.configure_virtual_sinks.assert_not_called()
