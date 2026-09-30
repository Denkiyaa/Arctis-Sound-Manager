# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""OLED packets per base station, as each one's SteelSeries spec lays them out.

The Nova Pro (Wireless/Wired) keeps the 1024-byte frames it has always been
driven with on real hardware. The Nova Elite family draws with 1036-byte
chunks and refuses brightness 0; the Arctis Pro GameDAC takes the whole
128x52 panel in one frame whose header is fixed at 52x128.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from arctis_sound_manager.config import load_device_configurations
from arctis_sound_manager.oled_manager import OledManager
from arctis_sound_manager.oled_protocol import OledProtocol


def _oled(name: str):
    for config in load_device_configurations():
        if config.name == name:
            return config.oled
    raise AssertionError(f"no profile named {name!r}")


def _protocol(cfg) -> OledProtocol:
    return OledProtocol(
        report_id=cfg.report_id, width=cfg.width, height=cfg.height,
        frame_report_size=cfg.frame_report_size,
        control_report_size=cfg.control_report_size,
        min_brightness=cfg.min_brightness, transpose=cfg.transpose,
    )


def _blank(width: int, height: int) -> bytearray:
    return bytearray(((width + 7) // 8) * height)


def _light(image: bytearray, width: int, x: int, y: int) -> None:
    image[y * ((width + 7) // 8) + x // 8] |= 1 << (7 - x % 8)


@pytest.mark.parametrize("name", [
    "SteelSeries Arctis Nova Elite", "SteelSeries Arctis Nova Pro Omni",
])
def test_elite_family_frames_are_1036_byte_chunks_split_in_two(name):
    proto = _protocol(_oled(name))
    packets = proto.build_frame_packets(bytes(1024), 128, 64)
    assert [len(p) for p in packets] == [1036, 1036]
    # The spec's sub-payloads: w1 = w/2 at x 0, the rest at x w1.
    assert packets[0][:6] == [0x01, 0x93, 0, 0, 64, 64]
    assert packets[1][:6] == [0x01, 0x93, 64, 0, 64, 64]


@pytest.mark.parametrize("name", [
    "SteelSeries Arctis Nova Elite", "SteelSeries Arctis Nova Pro Omni",
])
def test_elite_family_brightness_never_goes_below_one(name):
    proto = _protocol(_oled(name))
    assert not proto.can_go_dark
    assert proto.build_brightness_packet(0)[:3] == [0x01, 0x85, 1]
    assert len(proto.build_brightness_packet(5)) == 64


@pytest.mark.parametrize("name", [
    "SteelSeries Arctis Nova Pro Wireless", "SteelSeries Arctis Nova Pro Wired",
])
def test_nova_pro_keeps_the_frames_that_work_on_hardware(name):
    proto = _protocol(_oled(name))
    assert [len(p) for p in proto.build_frame_packets(bytes(1024), 128, 64)] == [1024, 1024]
    assert proto.can_go_dark
    assert proto.build_brightness_packet(0)[:3] == [0x06, 0x85, 0]


def test_pro_gamedac_sends_one_unnumbered_frame_with_the_spec_header():
    proto = _protocol(_oled("SteelSeries Arctis Pro GameDAC"))
    packets = proto.build_frame_packets(bytes(832), 128, 52)
    assert len(packets) == 1
    # HIDFEATURE 1025 with report id 0x00: the id byte is not on the wire.
    assert len(packets[0]) == 1024
    assert packets[0][:5] == [0x93, 0, 0, 52, 128]


def test_pro_gamedac_packs_each_panel_row_as_a_controller_column():
    proto = _protocol(_oled("SteelSeries Arctis Pro GameDAC"))
    image = _blank(128, 52)
    _light(image, 128, x=0, y=0)
    _light(image, 128, x=9, y=3)
    _light(image, 128, x=127, y=51)
    body = proto.build_frame_packets(bytes(image), 128, 52)[0][5:]
    # 52 columns of 128 rows: 16 bytes per column, LSB = lowest row.
    lit = {(i // 16, (i % 16) * 8 + bit)
           for i, byte in enumerate(body[:832]) for bit in range(8) if byte >> bit & 1}
    assert lit == {(0, 0), (3, 9), (51, 127)}
    assert not any(body[832:])


def test_pro_gamedac_control_reports_are_64_bytes_on_the_wire():
    proto = _protocol(_oled("SteelSeries Arctis Pro GameDAC"))
    assert proto.build_brightness_packet(4)[:2] == [0x85, 4]
    assert len(proto.build_brightness_packet(4)) == 64
    assert proto.build_return_to_ui_packet()[0] == 0x95


def _manager(monkeypatch, oled_cfg) -> tuple[OledManager, list]:
    core = SimpleNamespace(
        usb_device=None,
        _usb_write_lock=threading.Lock(),
        device_config=SimpleNamespace(oled=oled_cfg),
        general_settings=SimpleNamespace(oled_off_when_away=False, oled_brightness=7),
    )
    manager = OledManager(core)
    sent: list = []
    monkeypatch.setattr(manager, "_send_oled_packet",
                        lambda packet, control=False: sent.append((packet, control)) or True)
    return manager, sent


def test_going_dark_draws_black_where_brightness_zero_is_refused(monkeypatch):
    manager, sent = _manager(monkeypatch, _oled("SteelSeries Arctis Nova Elite"))
    manager._go_dark()
    assert sent and all(not control for _, control in sent)
    assert all(p[1] == 0x93 and not any(p[6:]) for p, _ in sent)


def test_going_dark_dims_to_zero_where_the_panel_accepts_it(monkeypatch):
    manager, sent = _manager(monkeypatch, _oled("SteelSeries Arctis Nova Pro Wireless"))
    manager._go_dark()
    assert [(p[:3], control) for p, control in sent] == [([0x06, 0x85, 0], True)]


def test_pro_gamedac_renders_at_its_own_panel_height(monkeypatch):
    manager, _ = _manager(monkeypatch, _oled("SteelSeries Arctis Pro GameDAC"))
    assert (manager._renderer.WIDTH, manager._renderer.HEIGHT) == (128, 52)
    assert len(manager._renderer.render_splash_image()) == 16 * 52
