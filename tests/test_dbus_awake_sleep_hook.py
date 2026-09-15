# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for scripts/dbus_awake.py's PrepareForSleep hook (#238).

Before this fix, resume replayed init_device() over the SAME libusb handle
that just survived suspend — which on the Nova Pro Omni left ChatMix dead
until a physical replug. The hook now delegates to CoreEngine.prepare_for_sleep
/ resume_from_sleep instead of teardown()/init_device() directly, without
disturbing the existing #128 post-wake routing reconciliation (+3s / +5s).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arctis_sound_manager.scripts.dbus_awake import DbusAwake


def _make_awake(core_engine) -> DbusAwake:
    awake = DbusAwake()
    awake.log = MagicMock()
    awake.core_engine = core_engine
    return awake


# ── going to sleep ───────────────────────────────────────────────────────────


def test_going_to_sleep_calls_prepare_for_sleep_not_teardown():
    core_engine = MagicMock()
    awake = _make_awake(core_engine)

    awake.on_prepare_for_sleep(True)

    core_engine.prepare_for_sleep.assert_called_once()
    core_engine.teardown.assert_not_called()


# ── waking up ────────────────────────────────────────────────────────────────


async def _drive_wake(core_engine) -> None:
    awake = _make_awake(core_engine)
    with patch.object(DbusAwake, '_WAKE_SETTLE_S', 0.0), \
         patch.object(DbusAwake, '_WAKE_RECHECK_S', 0.0):
        awake.on_prepare_for_sleep(False)
        # Give the tasks on_prepare_for_sleep scheduled a chance to run to
        # completion (both delays are patched to 0 above).
        for _ in range(10):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)


def test_waking_up_calls_resume_from_sleep_not_bare_init_device():
    core_engine = MagicMock()
    core_engine.resume_from_sleep = AsyncMock()

    asyncio.run(_drive_wake(core_engine))

    core_engine.resume_from_sleep.assert_called_once()
    core_engine.init_device.assert_not_called()


def test_waking_up_always_schedules_post_wake_reconciliation():
    """Guard #128: the +3s / +5s reconciliation must fire regardless of the
    #238 resume path added alongside it."""
    core_engine = MagicMock()
    core_engine.resume_from_sleep = AsyncMock()

    asyncio.run(_drive_wake(core_engine))

    assert core_engine.reconcile_audio_routing_for_power_state.call_count == 2


def test_waking_up_reconciliation_survives_resume_from_sleep_failure():
    """A #238 resume failure must not swallow the #128 reconciliation."""
    core_engine = MagicMock()
    core_engine.resume_from_sleep = AsyncMock(side_effect=RuntimeError("boom"))

    asyncio.run(_drive_wake(core_engine))

    assert core_engine.reconcile_audio_routing_for_power_state.call_count == 2


# ── _reconcile_routing_after_wake in isolation ──────────────────────────────


def test_reconcile_routing_after_wake_awaits_both_delays_in_order():
    core_engine = MagicMock()
    awake = _make_awake(core_engine)

    seen_delays: list[float] = []

    async def fake_sleep(delay):
        seen_delays.append(delay)

    with patch('asyncio.sleep', side_effect=fake_sleep):
        asyncio.run(awake._reconcile_routing_after_wake())

    assert seen_delays == [DbusAwake._WAKE_SETTLE_S, DbusAwake._WAKE_RECHECK_S]
    assert core_engine.reconcile_audio_routing_for_power_state.call_count == 2
