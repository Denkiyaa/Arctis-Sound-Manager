# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for radio_reconnect_probe_delay_ms: SteelSeries' own GG engine
retries a status read (get_fw_version, or the full settings sync on the
GameBuds) up to 10 times after the *wireless* headset reconnects its radio
link, instead of trusting a single read — "the first couple tries don't
work" per its own comment. ASM's replay_device_settings() used to fire a
single flat-delay attempt with no retry-on-no-answer.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_engine(radio_reconnect_probe_delay_ms):
    from arctis_sound_manager.core import CoreEngine

    dc = MagicMock()
    dc.radio_reconnect_probe_delay_ms = radio_reconnect_probe_delay_ms
    dc.status = MagicMock(request=0xb0)

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.device_config = dc
    engine.usb_device = MagicMock()
    engine.request_device_status = MagicMock()
    engine._await_raw_response = AsyncMock(return_value=[0xb0, 1, 2])
    return engine, dc


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    import arctis_sound_manager.core as core_mod
    monkeypatch.setattr(core_mod.time, "sleep", MagicMock())


def test_no_delay_configured_skips_the_probe_entirely():
    """radio_reconnect_probe_delay_ms unset ⇒ no probe, no sleep, no loop —
    the legacy single-attempt behaviour for every other family."""
    from arctis_sound_manager.core import CoreEngine

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=None)
    engine._main_event_loop = MagicMock()

    CoreEngine._wait_for_device_ready_after_radio_reconnect(engine)

    engine.request_device_status.assert_not_called()


def test_no_event_loop_captured_yet_is_a_safe_noop():
    """start() hasn't run yet (or this is a unit test with no daemon loop)
    ⇒ must not crash trying to schedule a coroutine on None."""
    from arctis_sound_manager.core import CoreEngine

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=100)
    engine._main_event_loop = None

    CoreEngine._wait_for_device_ready_after_radio_reconnect(engine)  # must not raise

    engine.request_device_status.assert_not_called()


def test_stops_retrying_once_the_device_answers():
    """A real asyncio loop running in a background thread, so
    run_coroutine_threadsafe has somewhere real to schedule onto — the
    probe succeeds on the first attempt, so there must be exactly one."""
    from arctis_sound_manager.core import CoreEngine

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=100)

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    engine._main_event_loop = loop
    try:
        CoreEngine._wait_for_device_ready_after_radio_reconnect(engine)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    engine.request_device_status.assert_called_once()
    engine.logger.warning.assert_not_called()


def test_gives_up_after_ten_attempts_but_still_returns():
    """The device never answers ⇒ exactly _RADIO_RECONNECT_PROBE_ATTEMPTS
    attempts, then it gives up (logs a warning) rather than looping forever
    or raising — replay_device_settings() must proceed regardless, matching
    GG's own "give up after 10, use whatever we have" behaviour."""
    from arctis_sound_manager.core import CoreEngine, _RADIO_RECONNECT_PROBE_ATTEMPTS

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=1)  # keep it fast
    engine._await_raw_response = AsyncMock(return_value=None)  # never answers

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    engine._main_event_loop = loop
    try:
        CoreEngine._wait_for_device_ready_after_radio_reconnect(engine)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)
        loop.close()

    assert engine.request_device_status.call_count == _RADIO_RECONNECT_PROBE_ATTEMPTS
    engine.logger.warning.assert_called_once()


def test_probe_status_once_returns_true_for_a_family_the_matcher_cant_key_on():
    """Same limitation as _probe_device_awake() (#238): a report-id-prefixed
    status.request can't be matched by the raw-response waiter, so this
    treats the family as answered rather than burning the whole retry
    budget on a probe that can never succeed."""
    import asyncio as _asyncio
    from arctis_sound_manager.core import CoreEngine

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=100)
    dc.status = MagicMock(request=0x01b0)  # > 0xFF, report-id prefixed

    result = _asyncio.run(CoreEngine._probe_status_once(engine, timeout=0.1))

    assert result is True
    engine.request_device_status.assert_not_called()


def test_replay_device_settings_calls_the_probe_before_pushing_anything():
    """The probe must run before _send_device_init_sequence(), not after —
    otherwise it is just wasted time instead of a real gate."""
    from arctis_sound_manager.core import CoreEngine

    engine, dc = _make_engine(radio_reconnect_probe_delay_ms=None)
    engine._device_lock = threading.RLock()
    engine._send_device_init_sequence = MagicMock()
    engine._replay_settings_missing_from_init = MagicMock()
    engine.reconcile_hardware_eq_mode = MagicMock()
    engine._applied_eq_mode = "sonar"

    order = []
    engine._wait_for_device_ready_after_radio_reconnect = MagicMock(
        side_effect=lambda: order.append("probe"))
    engine._send_device_init_sequence.side_effect = lambda **kw: order.append("replay")

    CoreEngine.replay_device_settings(engine)

    assert order == ["probe", "replay"]
