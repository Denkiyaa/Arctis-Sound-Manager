# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for shutdown_commands: SteelSeries' own GG engine sends
disable-chatmix/disable-sonar to the "Sonar integrated device" family
before releasing the interface, so the base station falls back to plain
hardware volume instead of being left in software-ChatMix mode with
nobody driving it once the host goes away. ASM's teardown() never did
this at all.
"""

import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import usb.core
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _make_engine(device_config):
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.usb_device = MagicMock(name="usb_device")
    engine.device_config = device_config
    engine.get_command_endpoint_address = MagicMock(return_value=0)
    engine.translate_init_bytes = lambda b: list(b)
    engine.send_command = MagicMock()
    return engine


def _cfg(shutdown_commands):
    cfg = MagicMock()
    cfg.shutdown_commands = shutdown_commands
    return cfg


def test_no_shutdown_commands_sends_nothing():
    engine = _make_engine(_cfg(None))
    CoreEngine._send_shutdown_commands(engine)
    engine.send_command.assert_not_called()


def test_empty_shutdown_commands_sends_nothing():
    engine = _make_engine(_cfg([]))
    CoreEngine._send_shutdown_commands(engine)
    engine.send_command.assert_not_called()


def test_shutdown_commands_sent_in_order():
    engine = _make_engine(_cfg([[0x01, 0x49, 0x00], [0x01, 0x8d, 0x00]]))
    CoreEngine._send_shutdown_commands(engine)
    assert engine.send_command.call_args_list == [
        call([0x01, 0x49, 0x00], 0),
        call([0x01, 0x8d, 0x00], 0),
    ]


def test_a_failed_shutdown_command_does_not_raise_or_stop_the_rest():
    engine = _make_engine(_cfg([[0x01, 0x49, 0x00], [0x01, 0x8d, 0x00]]))
    engine.send_command.side_effect = [usb.core.USBError("boom"), None]

    CoreEngine._send_shutdown_commands(engine)  # must not raise

    assert engine.send_command.call_count == 2
    engine.logger.warning.assert_called_once()


def test_no_usb_device_sends_nothing():
    engine = _make_engine(_cfg([[0x01, 0x49, 0x00]]))
    engine.usb_device = None
    CoreEngine._send_shutdown_commands(engine)
    engine.send_command.assert_not_called()


def test_teardown_sends_shutdown_commands_before_releasing_the_interface():
    """Order matters: the interface must still be claimed when these are
    sent, so _send_shutdown_commands() has to run before
    release_all_interfaces()."""
    engine = _make_engine(_cfg([[0x01, 0x49, 0x00]]))
    engine._device_lock = threading.RLock()
    engine.release_all_interfaces = MagicMock()
    engine.kernel_attach = MagicMock()
    engine.redirect_audio_on_disconnect = MagicMock()
    engine.loopback_manager = MagicMock()
    engine.oled_manager = None

    order = []
    engine.send_command.side_effect = lambda *a: order.append("shutdown_command")
    engine.release_all_interfaces.side_effect = lambda *a: order.append("release_interfaces")

    with patch("usb.core.find", return_value=None):
        CoreEngine.teardown(engine)

    assert order == ["shutdown_command", "release_interfaces"]


# ── the five profiles that declare shutdown_commands ────────────────────────

@pytest.mark.parametrize("profile, expected", [
    ("nova_pro_omni.yaml", [[0x01, 0x49, 0x00], [0x01, 0x8d, 0x00]]),
    ("nova_elite.yaml", [[0x01, 0x49, 0x00], [0x01, 0x8d, 0x00]]),
    ("nova_pro_wireless.yaml", [[0x06, 0x49, 0x00], [0x06, 0x8d, 0x00]]),
    ("arctis_nova_pro_wired.yaml", [[0x06, 0x49, 0x00]]),
    ("nova_3_wireless.yaml", [[0x49, 0x00]]),
])
def test_profile_shutdown_commands_mirror_its_own_enable_commands(profile, expected):
    """Each disable command must use the exact same report-id prefix as the
    matching enable command already in device_init — a mismatched prefix
    would land on the wrong report and be silently dropped (the #146 class
    of bug), same as any other command on this transport."""
    raw = _yaml.load((DEVICES_DIR / profile).read_text())
    config = DeviceConfiguration(raw)
    assert config.shutdown_commands == expected


@pytest.mark.parametrize("profile", sorted(
    p.name for p in DEVICES_DIR.glob("*.yaml")
    if p.name not in {
        "nova_pro_omni.yaml", "nova_elite.yaml", "nova_pro_wireless.yaml",
        "arctis_nova_pro_wired.yaml", "nova_3_wireless.yaml",
    }))
def test_other_profiles_declare_no_shutdown_commands(profile):
    """Only the "Sonar integrated device" family enables anything that needs
    undoing on shutdown — every other profile must stay None, not an
    invented command nobody asked for."""
    raw = _yaml.load((DEVICES_DIR / profile).read_text())
    config = DeviceConfiguration(raw)
    assert config.shutdown_commands is None
