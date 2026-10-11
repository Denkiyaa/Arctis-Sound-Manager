# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #321: Nova Pro Wired - knob chaos with the OLED on, lag with it off.

Two causes, both fixed here:

* the profile talked to the wired DAC in the Nova Pro *Wireless* base's
  vocabulary (status poll 0xB0, muted-mic LED 0xBF), opcodes the wired spec
  does not define; the DAC answers an unknown SET_REPORT with a STALL
  ([Errno 32] Pipe error);
* listen_endpoint_loop() slept 100 ms after every frame, draining a burst of
  wheel reports at ten per second while usbhid drains them at once.
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from unittest.mock import MagicMock

import pytest

import arctis_sound_manager.core as core_mod
from arctis_sound_manager.config import load_device_configurations
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.oled_protocol import OledProtocol

# base_arctis_nova_pro.device (GG 121.0.0): every command byte the family
# defines. Anything else gets a STALL.
SPEC_OPCODES = {
    0x01, 0x09, 0x10, 0x12, 0x20, 0x27, 0x2E, 0x33, 0x37, 0x39, 0x43, 0x47,
    0x49, 0x80, 0x83, 0x85, 0x8D, 0x93, 0x95,
}


def _wired():
    for c in load_device_configurations():
        if c.name == "SteelSeries Arctis Nova Pro Wired":
            return c
    raise AssertionError("Nova Pro Wired profile not found")


def _opcode(seq):
    seq = list(seq)
    assert seq[0] == 0x06, f"{seq}: not report id 0x06"
    return seq[1]


def test_every_command_of_the_wired_profile_is_in_the_spec():
    cfg = _wired()
    commands = [list(c) for c in cfg.device_init if c[0] != "sleep"]
    commands = [[cfg.status.request >> 8, cfg.status.request & 0xFF]
                if c == ["status.request"] else c for c in commands]
    commands += [list(c) for c in (cfg.shutdown_commands or [])]
    commands.append(list(cfg.hardware_eq_command))
    commands.append(list(cfg.hardware_eq_preset_select))
    commands.append([cfg.status.request >> 8, cfg.status.request & 0xFF])
    for section in cfg.settings.values():
        commands += [list(s.update_sequence) for s in section]
    for extra in cfg.status.extra_requests:
        commands.append([extra >> 8, extra & 0xFF])

    stray = {hex(_opcode(c)) for c in commands} - {hex(o) for o in SPEC_OPCODES}
    assert not stray, f"opcodes the wired family does not define: {sorted(stray)}"


def test_wired_profile_no_longer_polls_or_maps_the_wireless_b0_frame():
    cfg = _wired()
    assert cfg.status.request == 0x0620
    assert all(m.starts_with != 0x06B0 for m in cfg.status.response_mapping)
    names = {s.name for section in cfg.settings.values() for s in section}
    assert "mic_led_brightness" not in names


def test_wired_oled_follows_the_spec_geometry():
    oled = _wired().oled
    assert (oled.width, oled.height, oled.y_offset) == (128, 52, 12)
    assert oled.frame_report_size == 521 and oled.reload_on_exit
    proto = OledProtocol(
        report_id=oled.report_id, width=oled.width, height=oled.height,
        frame_report_size=oled.frame_report_size,
        control_report_size=oled.control_report_size, y_offset=oled.y_offset)
    packets = proto.build_frame_packets(bytes(16 * 52), 128, 52)
    assert [len(p) for p in packets] == [521, 521]
    assert packets[0][:6] == [0x06, 0x93, 0, 12, 64, 56]
    assert packets[1][:6] == [0x06, 0x93, 64, 12, 64, 56]


def test_other_profiles_keep_drawing_at_row_zero():
    wireless = next(c for c in load_device_configurations()
                    if c.name == "SteelSeries Arctis Nova Pro Wireless")
    assert wireless.oled.y_offset == 0 and not wireless.oled.reload_on_exit
    assert wireless.oled.frame_report_size == 1024


# --- listen loop ---------------------------------------------------------

def _engine(read):
    engine = MagicMock()
    engine.device_config = _wired()
    engine.device_status = {}
    engine._device_lock = threading.RLock()
    engine._untagged_requests_pending = deque()
    engine._eio_counts = {}
    engine._enodev_count = 0
    engine.guess_interface_endpoint.return_value = (0x88, 16)
    engine.usb_device.read.side_effect = read
    return engine


def _run_recording_sleeps(engine, monkeypatch, iterations):
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *a, **k):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(core_mod.asyncio, "sleep", fake_sleep)

    async def go():
        for _ in range(iterations):
            await CoreEngine.listen_endpoint_loop(engine, 4)

    asyncio.run(go())
    return sleeps


def test_a_burst_of_wheel_reports_is_drained_without_waiting(monkeypatch):
    frames = [[0x07, 0x25, v] + [0] * 13 for v in (50, 49, 48, 47, 46)]
    engine = _engine(lambda *a, **k: frames.pop(0))

    sleeps = _run_recording_sleeps(engine, monkeypatch, 5)

    assert engine.usb_device.read.call_count == 5
    assert all(s == 0 for s in sleeps), (
        f"listen loop slept {sleeps} between back-to-back reports (#321)")
    assert engine.device_status["station_volume"] == 46


def test_no_busy_loop_when_the_configuration_is_gone(monkeypatch):
    engine = _engine(lambda *a, **k: [0x07, 0x25, 10] + [0] * 13)
    engine.device_config = None

    sleeps = _run_recording_sleeps(engine, monkeypatch, 1)

    assert sleeps and max(sleeps) >= core_mod._LISTEN_IDLE_BACKOFF_S


def test_no_busy_loop_when_the_device_is_gone(monkeypatch):
    engine = _engine(lambda *a, **k: [0] * 16)
    engine.usb_device = None
    assert max(_run_recording_sleeps(engine, monkeypatch, 1)) >= 0.1


@pytest.mark.parametrize("errno_val,minimum", [(16, 1.0), (19, 1.0), (5, 0.5)])
def test_error_paths_still_back_off(monkeypatch, errno_val, minimum):
    import usb.core

    def boom(*a, **k):
        raise usb.core.USBError("x", errno=errno_val)

    engine = _engine(boom)
    engine.on_device_disconnected = MagicMock()
    sleeps = _run_recording_sleeps(engine, monkeypatch, 1)
    assert max(sleeps) >= minimum
