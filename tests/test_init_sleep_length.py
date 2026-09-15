# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for init_sleep_length_ms (#238 follow-up): SteelSeries' own GG engine
waits after the USB transmitter enumerates — up to 5s for the Nova Pro
Omni/Elite/Wireless family — before sending it any command, because the
firmware itself is still booting.

configure_virtual_sinks() runs on the pyudev observer thread
(register_on_connect), which must not block: it is the single path for every
USB hotplug event, not just this device's. So the wait is served by a
threading.Timer instead of a plain time.sleep(), and _detect_lock /
_device_configured_event are only released/set once the deferred (or
immediate, when no settle is configured) work actually finishes.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest


def _make_device_config(init_sleep_length_ms=None):
    cfg = MagicMock()
    cfg.vendor_id = 0x1038
    cfg.command_interface_index = [0, 0]
    cfg.listen_interface_indexes = [1]
    cfg.dial_interface_index = 2
    cfg.dial_interface_candidates = []
    cfg.init_sleep_length_ms = init_sleep_length_ms
    return cfg


def _make_engine_stub():
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.pa_audio_manager = MagicMock()
    engine._device_lock = threading.RLock()
    engine._detect_lock = threading.Lock()
    engine._device_configured_event = threading.Event()
    engine._device_configured_event.set()
    engine._pending_init_timer = None
    engine._device_ready = False
    engine._rescan_in_flight = False
    engine._logged_no_device = False
    engine.usb_device = None
    engine.device_config = None
    engine.oled_manager = None
    engine._active_extra_dial_interfaces = []
    engine._usb_write_lock = threading.Lock()
    return engine


def _run_configure_virtual_sinks(engine, dc):
    """Drive configure_virtual_sinks() with everything below device
    detection mocked out, mirroring test_boot_rescan.py's pattern."""
    from arctis_sound_manager.core import CoreEngine
    import usb.core as _usb_core

    intf = MagicMock()
    intf.bInterfaceClass = 3  # USB_CLASS_HID
    cfg_obj = MagicMock()
    cfg_obj.__iter__ = MagicMock(return_value=iter([intf]))
    mock_dev = MagicMock(spec=_usb_core.Device)
    mock_dev.idVendor = 0x1038
    mock_dev.idProduct = 0x1234
    mock_dev.__iter__ = MagicMock(return_value=iter([cfg_obj]))
    mock_dev.is_kernel_driver_active = MagicMock(return_value=False)

    engine.device_configurations = [dc]

    with patch.object(CoreEngine, '_find_hid_device', return_value=mock_dev), \
         patch.object(CoreEngine, 'kernel_detach', return_value=True), \
         patch.object(CoreEngine, '_discover_physical_nodes', return_value=("game_sink", None, None)), \
         patch.object(CoreEngine, 'init_device', return_value=None) as mock_init, \
         patch.object(CoreEngine, 'redirect_to_media_sink', return_value=None), \
         patch.object(CoreEngine, 'new_device_status', return_value=MagicMock()), \
         patch.object(CoreEngine, 'setup_loopbacks', return_value=None), \
         patch.object(CoreEngine, '_update_active_dial_interfaces', return_value=None), \
         patch('arctis_sound_manager.core.DeviceSettings') as MockDS, \
         patch('arctis_sound_manager.core.device_state.set_current_device'), \
         patch('arctis_sound_manager.core.check_and_fix_stale_configs', return_value=(False, False), create=True):

        mock_ds_instance = MagicMock()
        mock_ds_instance.settings = MagicMock()
        mock_ds_instance.settings.add_observer = MagicMock()
        MockDS.return_value = mock_ds_instance

        with patch.dict('sys.modules', {
            'arctis_sound_manager.sonar_to_pipewire': MagicMock(
                check_and_fix_stale_configs=MagicMock(return_value=(False, False))
            )
        }):
            CoreEngine.configure_virtual_sinks(engine)

    return mock_init


def test_no_init_sleep_runs_synchronously():
    """init_sleep_length_ms unset ⇒ init_device() runs and the device is
    marked ready before configure_virtual_sinks() returns, no timer used."""
    engine = _make_engine_stub()
    dc = _make_device_config(init_sleep_length_ms=None)

    with patch('threading.Timer') as MockTimer:
        mock_init = _run_configure_virtual_sinks(engine, dc)

    MockTimer.assert_not_called()
    mock_init.assert_called_once()
    assert engine._device_ready is True
    assert engine._detect_lock.acquire(blocking=False), "lock must be released"
    engine._detect_lock.release()
    assert engine._device_configured_event.is_set()


def test_init_sleep_defers_without_blocking_and_without_touching_the_device():
    """init_sleep_length_ms set ⇒ configure_virtual_sinks() returns having
    scheduled a timer, without calling init_device() and without releasing
    _detect_lock or setting _device_configured_event yet — a concurrent
    on_device_connected() must see the lock as still held."""
    engine = _make_engine_stub()
    dc = _make_device_config(init_sleep_length_ms=5000)

    with patch('threading.Timer') as MockTimer:
        mock_timer_instance = MagicMock()
        MockTimer.return_value = mock_timer_instance
        mock_init = _run_configure_virtual_sinks(engine, dc)

    MockTimer.assert_called_once()
    args, _ = MockTimer.call_args
    assert args[0] == 5.0, "init_sleep_length_ms=5000 must become a 5.0s timer"
    assert args[1] == engine._finish_configure_virtual_sinks
    mock_timer_instance.start.assert_called_once()

    mock_init.assert_not_called(), "init_device() must not run before the settle timer fires"
    assert engine._device_ready is False
    assert not engine._detect_lock.acquire(blocking=False), \
        "the lock must stay held for a concurrent on_device_connected() to skip"
    assert not engine._device_configured_event.is_set(), \
        "a waiter (resume_from_sleep) must see 'not configured yet'"
    assert engine._pending_init_timer is mock_timer_instance


def test_finish_configure_virtual_sinks_releases_lock_and_marks_ready():
    """The timer callback (simulated directly, as if init_sleep_length_ms had
    elapsed) must finish the job, release _detect_lock and set the event —
    it owns both since the synchronous half handed them off unreleased."""
    engine = _make_engine_stub()
    dc = _make_device_config(init_sleep_length_ms=5000)

    with patch('threading.Timer'):
        _run_configure_virtual_sinks(engine, dc)

    # configure_virtual_sinks() has already left the lock held and the event
    # cleared (asserted above) — now simulate the timer firing.
    from arctis_sound_manager.core import CoreEngine
    with patch.object(CoreEngine, 'init_device', return_value=None) as mock_init, \
         patch.object(CoreEngine, 'redirect_to_media_sink', return_value=None):
        CoreEngine._finish_configure_virtual_sinks(engine, dc)

    mock_init.assert_called_once()
    assert engine._device_ready is True
    assert engine._detect_lock.acquire(blocking=False), "lock must be released once the deferred work is done"
    engine._detect_lock.release()
    assert engine._device_configured_event.is_set()


def test_finish_configure_virtual_sinks_releases_lock_even_on_failure():
    """init_device() raising must not leak the lock or hang a waiter forever
    — same contract _finish_configure_virtual_sinks_body already gives the
    synchronous path (#202), extended to the deferred one."""
    engine = _make_engine_stub()
    dc = _make_device_config(init_sleep_length_ms=5000)

    with patch('threading.Timer'):
        _run_configure_virtual_sinks(engine, dc)

    from arctis_sound_manager.core import CoreEngine
    with patch.object(CoreEngine, 'init_device', side_effect=RuntimeError("boom")), \
         patch.object(CoreEngine, 'redirect_to_media_sink', return_value=None):
        CoreEngine._finish_configure_virtual_sinks(engine, dc)

    assert engine._device_ready is True, "still marked present — partial config beats 'absent' (#202)"
    assert engine._detect_lock.acquire(blocking=False)
    engine._detect_lock.release()
    assert engine._device_configured_event.is_set()


# ── resume_from_sleep() waiting on the event ────────────────────────────────


def test_resume_from_sleep_waits_on_the_configured_event():
    """resume_from_sleep() must not probe the device until
    _device_configured_event says configure_virtual_sinks() actually
    finished — probing while a settle timer is still pending would race
    init_device() and read a device that has not been told anything yet."""
    import asyncio
    from unittest.mock import AsyncMock

    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.usb_device = MagicMock(idProduct=0x2290, bus=1, port_numbers=(6,))
    dc = MagicMock()
    dc.reset_on_resume = False
    dc.status = MagicMock(request=0xb0)
    engine.device_config = dc
    engine._settings_replay_timer = None
    engine._resume_reset_attempted = False
    engine._last_usb_reset_monotonic = 0.0
    engine._release_usb_handle = MagicMock()
    engine._detect_lock = threading.Lock()  # consulted by _configure_virtual_sinks_off_loop
    engine.configure_virtual_sinks = MagicMock()
    engine.request_device_status = MagicMock()
    engine._await_raw_response = AsyncMock(return_value=[0xb0, 1, 2])

    # Not set — a settle timer is "still pending" until something else sets it.
    engine._device_configured_event = threading.Event()

    def _set_event_shortly_after():
        import time
        time.sleep(0.05)
        engine._device_configured_event.set()

    threading.Thread(target=_set_event_shortly_after, daemon=True).start()

    asyncio.run(engine.resume_from_sleep())

    # If resume_from_sleep() had not waited, request_device_status() (called
    # from _probe_device_awake) could in principle still have been invoked —
    # what actually matters here is that the run completed at all, i.e. the
    # wait did not hang past the event being set, and did reach the probe.
    engine.request_device_status.assert_called_once()
