# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for issue #128 — audio routing not reconciled after resume from sleep.

With the headset powered off, resuming from sleep only ran init_device()
(USB re-init + EQ), never re-running the connect/disconnect redirect logic.
PipeWire/WirePlumber re-links each stream to its remembered target.node once
the graph settles after resume, so media apps snap back onto Arctis_Media
even though the headset is off, because the device status never changes
(offline -> offline) and on_device_status_changed() never fires.

Fixed by:
  * CoreEngine.reconcile_audio_routing_for_power_state(), which re-asserts the
    correct redirect for the current online/offline state.
  * DbusAwake.on_prepare_for_sleep(), which schedules
    _reconcile_routing_after_wake() (two delayed passes) alongside the resume
    task.

Issue #238 later replaced the bare init_device() call on wake with
CoreEngine.resume_from_sleep() (see test_dbus_awake_sleep_hook.py and
test_resume_recovery.py) — the #128 reconciliation task asserted here must
keep firing regardless of that change, which is what
test_on_prepare_for_sleep_wake_schedules_resume_and_reconcile checks.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── CoreEngine.reconcile_audio_routing_for_power_state() ───────────────────

def _make_engine(online):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine._device_lock = threading.Lock()
    engine.usb_device = object()
    engine.device_config = object()
    engine.is_device_online = MagicMock(return_value=online)
    engine.redirect_to_media_sink = MagicMock()
    engine.redirect_audio_on_disconnect = MagicMock()
    return engine


def test_reconcile_redirects_to_media_sink_when_online():
    engine = _make_engine(online=True)
    engine.reconcile_audio_routing_for_power_state()
    engine.redirect_to_media_sink.assert_called_once()
    engine.redirect_audio_on_disconnect.assert_not_called()


def test_reconcile_redirects_on_disconnect_when_offline():
    engine = _make_engine(online=False)
    engine.reconcile_audio_routing_for_power_state()
    engine.redirect_audio_on_disconnect.assert_called_once()
    engine.redirect_to_media_sink.assert_not_called()


def test_reconcile_noop_when_no_device():
    engine = _make_engine(online=False)
    engine.usb_device = None
    engine.reconcile_audio_routing_for_power_state()
    engine.redirect_to_media_sink.assert_not_called()
    engine.redirect_audio_on_disconnect.assert_not_called()


# ── DbusAwake.on_prepare_for_sleep() / _reconcile_routing_after_wake() ─────

def _make_dbus_awake():
    from arctis_sound_manager.scripts.dbus_awake import DbusAwake

    d = DbusAwake.__new__(DbusAwake)
    d.log = MagicMock()
    d.core_engine = MagicMock()
    d.core_engine.resume_from_sleep = AsyncMock()
    return d


def test_on_prepare_for_sleep_going_to_sleep_calls_prepare_for_sleep_only():
    d = _make_dbus_awake()
    d.on_prepare_for_sleep(True)
    d.core_engine.prepare_for_sleep.assert_called_once()
    d.core_engine.init_device.assert_not_called()
    d.core_engine.teardown.assert_not_called()
    d.core_engine.reconcile_audio_routing_for_power_state.assert_not_called()


def test_on_prepare_for_sleep_wake_schedules_resume_and_reconcile():
    """#238 replaced the bare init_device() call with a resume_from_sleep()
    task; the #128 reconcile task must still be scheduled alongside it,
    independently (see the module docstring)."""
    async def _run():
        d = _make_dbus_awake()
        d._WAKE_SETTLE_S = 0
        d._WAKE_RECHECK_S = 0

        before = asyncio.all_tasks()
        d.on_prepare_for_sleep(False)
        d.core_engine.init_device.assert_not_called()

        scheduled = asyncio.all_tasks() - before
        assert len(scheduled) == 2
        await asyncio.gather(*scheduled)

        d.core_engine.resume_from_sleep.assert_called_once()
        assert d.core_engine.reconcile_audio_routing_for_power_state.call_count == 2

    asyncio.run(_run())


def test_reconcile_routing_after_wake_calls_reconcile_twice_per_delay():
    d = _make_dbus_awake()
    d._WAKE_SETTLE_S = 0
    d._WAKE_RECHECK_S = 0

    asyncio.run(d._reconcile_routing_after_wake())

    assert d.core_engine.reconcile_audio_routing_for_power_state.call_count == 2


def test_reconcile_routing_after_wake_logs_and_continues_on_error():
    d = _make_dbus_awake()
    d._WAKE_SETTLE_S = 0
    d._WAKE_RECHECK_S = 0
    d.core_engine.reconcile_audio_routing_for_power_state.side_effect = RuntimeError("boom")

    asyncio.run(d._reconcile_routing_after_wake())

    assert d.core_engine.reconcile_audio_routing_for_power_state.call_count == 2
    assert d.log.warning.call_count == 2
