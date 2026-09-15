# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for CoreEngine.resume_from_sleep() / prepare_for_sleep() (#238):
ChatMix stays dead on the Nova Pro Omni after a system suspend/resume until
the DAC is physically replugged, even though configure_virtual_sinks() (the
generic same-device re-enumeration path) runs cleanly. This adds an opt-in
escalation to a hard USB reset when either the profile declares
reset_on_resume, or a post-resume status probe times out.

Builds a CoreEngine via __new__ (as tests/test_usb_reenum_ebusy.py does) and
stubs only the attributes resume_from_sleep touches — never a real USB device.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_reset_settle_delay(monkeypatch):
    """_escalate_to_usb_reset() awaits _USB_RESET_SETTLE_S (~2s) after a
    successful reset before reconfiguring — skip the real wait in tests."""
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())


def _make_device_config(reset_on_resume: bool = False, status_request: int = 0xb0):
    """Minimal DeviceConfiguration mock.

    status_request <= 0xFF (e.g. 0xb0) exercises the generic status probe;
    > 0xFF (e.g. 0x01b0, the Nova Pro Omni's real value) mirrors a
    report-id-prefixed reply the raw-response matcher can't key on, so the
    probe is skipped and only reset_on_resume drives the escalation — see
    CoreEngine._probe_device_awake's docstring.
    """
    cfg = MagicMock()
    cfg.vendor_id = 0x1038
    cfg.reset_on_resume = reset_on_resume
    cfg.status = MagicMock()
    cfg.status.request = status_request
    return cfg


def _make_engine(device_config):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._device_lock = threading.RLock()
    engine._detect_lock = threading.Lock()  # consulted by _configure_virtual_sinks_off_loop
    engine.usb_device = MagicMock(name='usb_device', idProduct=0x2290, bus=1, port_numbers=(6,))
    engine.device_config = device_config
    engine.oled_manager = None
    engine._settings_replay_timer = None
    engine._resume_reset_attempted = False
    engine._last_usb_reset_monotonic = 0.0
    engine._release_usb_handle = MagicMock()
    engine._device_configured_event = threading.Event()
    engine._device_configured_event.set()
    engine._pending_init_timer = None
    engine.configure_virtual_sinks = MagicMock()
    engine.request_device_status = MagicMock()
    engine._await_raw_response = AsyncMock(return_value=[0xb0, 0, 0])
    return engine


# ── healthy resume, no opt-in ────────────────────────────────────────────────


def test_healthy_resume_without_reset_on_resume_never_resets():
    """Profile without reset_on_resume + a probe that answers ⇒ no reset."""
    dc = _make_device_config(reset_on_resume=False, status_request=0xb0)
    engine = _make_engine(dc)
    engine._await_raw_response = AsyncMock(return_value=[0xb0, 1, 2])

    with patch('arctis_sound_manager.core.reset_via_usbfs') as mock_reset:
        asyncio.run(engine.resume_from_sleep())

    engine.configure_virtual_sinks.assert_called_once()
    mock_reset.assert_not_called()


# ── reset_on_resume opt-in ───────────────────────────────────────────────────


def test_reset_on_resume_true_escalates_once_even_with_healthy_probe():
    """reset_on_resume: true (Nova Pro Omni) ⇒ reset regardless of the probe."""
    dc = _make_device_config(reset_on_resume=True, status_request=0x01b0)
    engine = _make_engine(dc)

    with patch('arctis_sound_manager.core.reset_via_usbfs', return_value=True) as mock_reset, \
         patch('usb.util.dispose_resources') as mock_dispose:
        asyncio.run(engine.resume_from_sleep())

    mock_reset.assert_called_once()
    mock_dispose.assert_called_once()
    assert engine.usb_device is None
    # Once for the initial re-acquire, once after the reset settles.
    assert engine.configure_virtual_sinks.call_count == 2


# ── probe timeout ────────────────────────────────────────────────────────────


def test_probe_timeout_triggers_reset_once():
    """No opt-in, but the status probe times out ⇒ escalate anyway."""
    dc = _make_device_config(reset_on_resume=False, status_request=0xb0)
    engine = _make_engine(dc)
    engine._await_raw_response = AsyncMock(return_value=None)  # timeout

    with patch('arctis_sound_manager.core.reset_via_usbfs', return_value=True) as mock_reset, \
         patch('usb.util.dispose_resources'):
        asyncio.run(engine.resume_from_sleep())

    mock_reset.assert_called_once()


# ── rate limiting ────────────────────────────────────────────────────────────


def test_two_resumes_within_min_interval_reset_only_once():
    """Two wake cycles inside _USB_RESET_MIN_INTERVAL_S ⇒ at most one reset,
    even though prepare_for_sleep() clears the per-cycle _resume_reset_attempted
    guard between them."""
    dc = _make_device_config(reset_on_resume=True, status_request=0x01b0)
    engine = _make_engine(dc)

    with patch('arctis_sound_manager.core.reset_via_usbfs', return_value=True) as mock_reset, \
         patch('usb.util.dispose_resources'):
        asyncio.run(engine.resume_from_sleep())
        assert mock_reset.call_count == 1

        # Simulate a new suspend/resume cycle happening right away.
        engine.usb_device = MagicMock(idProduct=0x2290, bus=1, port_numbers=(6,))
        engine.prepare_for_sleep()
        assert engine._resume_reset_attempted is False

        asyncio.run(engine.resume_from_sleep())

    mock_reset.assert_called_once()


def test_resume_reset_attempted_blocks_second_call_same_cycle():
    """_resume_reset_attempted alone (without the timing window) also stops a
    second attempt within the same wake cycle."""
    dc = _make_device_config(reset_on_resume=True, status_request=0x01b0)
    engine = _make_engine(dc)
    engine._resume_reset_attempted = True  # pretend one already happened

    with patch('arctis_sound_manager.core.reset_via_usbfs') as mock_reset:
        asyncio.run(engine.resume_from_sleep())

    mock_reset.assert_not_called()


# ── failed reset never escalates further ────────────────────────────────────


def test_reset_via_usbfs_failure_does_not_retry_or_touch_authorized():
    dc = _make_device_config(reset_on_resume=True, status_request=0x01b0)
    engine = _make_engine(dc)

    with patch('arctis_sound_manager.core.reset_via_usbfs', return_value=False) as mock_reset, \
         patch('usb.util.dispose_resources') as mock_dispose, \
         patch('arctis_sound_manager.usb_reenumerate.toggle_authorized') as mock_toggle:
        asyncio.run(engine.resume_from_sleep())

    mock_reset.assert_called_once()
    mock_dispose.assert_not_called()
    mock_toggle.assert_not_called()
    # Only the initial re-acquire — the post-reset re-configure never runs.
    engine.configure_virtual_sinks.assert_called_once()


# ── ordering on a successful reset ──────────────────────────────────────────


def test_dispose_then_reconfigure_order_on_successful_reset():
    dc = _make_device_config(reset_on_resume=True, status_request=0x01b0)
    engine = _make_engine(dc)

    order: list[str] = []
    engine.configure_virtual_sinks = MagicMock(
        side_effect=lambda: order.append('configure_virtual_sinks'))

    with patch('arctis_sound_manager.core.reset_via_usbfs', return_value=True), \
         patch('usb.util.dispose_resources', side_effect=lambda dev: order.append('dispose_resources')):
        asyncio.run(engine.resume_from_sleep())

    # First call is the pre-reset re-acquire, the interesting order is the
    # last two entries: dispose must precede the post-reset reconfigure.
    assert order == ['configure_virtual_sinks', 'dispose_resources', 'configure_virtual_sinks']
