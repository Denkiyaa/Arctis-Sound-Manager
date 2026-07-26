# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_hardware_eq.py — the custom EQ must not be sent to headsets without one.

send_eq_command() used to hardcode [0x06, 0x33] — the Nova Pro Wireless' report
id and EQ opcode — and send it to whatever was connected. Every other family
uses a different report id, a different opcode, or both, so the command was
discarded by the headset without any error and the custom EQ sliders did
nothing at all (#146, Arctis Nova 7P Gen 2).

Wire format for the families that do have it: one byte per band on a 0-40
scale, 20 being 0 dB — GG computes it as 2 * (10 + gain_in_dB).
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# Profiles whose protocol we know drives an on-device EQ, and the exact bytes
# that must lead the command.
EXPECTED_EQ_COMMANDS = {
    "nova_pro_wireless.yaml": [0x06, 0x33],
    "arctis_nova_pro_wired.yaml": [0x06, 0x33],
}

# Profiles whose EQ takes a parametric payload instead (see hardware_eq.py).
PARAMETRIC_PROFILES = {
    "nova_7_perc_battery.yaml",
    "nova_7p_perc_battery.yaml",
}


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _engine(config: DeviceConfiguration | None) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.logger = MagicMock()
    engine.get_command_endpoint_address.return_value = 0
    engine.send_eq_command = lambda bands: CoreEngine.send_eq_command(engine, bands)
    engine.has_hardware_eq = lambda: CoreEngine.has_hardware_eq(engine)
    return engine


@pytest.mark.parametrize("profile,expected", sorted(EXPECTED_EQ_COMMANDS.items()))
def test_declared_profiles_send_their_own_command(profile, expected):
    engine = _engine(_config(profile))
    bands = [20] * 10

    assert engine.send_eq_command(bands) is True
    engine.send_command.assert_called_once_with(expected + bands, 0)
    assert engine.has_hardware_eq() is True


@pytest.mark.parametrize("profile", [
    p.name for p in sorted(DEVICES.glob("*.yaml"))
    if p.name not in EXPECTED_EQ_COMMANDS and p.name not in PARAMETRIC_PROFILES
])
def test_other_profiles_send_nothing(profile):
    """No command at all beats a command the headset cannot understand."""
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()
    assert engine.has_hardware_eq() is False


@pytest.mark.parametrize("profile", sorted(PARAMETRIC_PROFILES))
def test_parametric_profiles_send_three_frames(profile):
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True
    assert engine.has_hardware_eq() is True
    opcodes = [call.args[0][1] for call in engine.send_command.call_args_list]
    assert opcodes == [0xA7, 0x33, 0x27]


def test_slider_scale_maps_onto_decibels():
    """ASM's 0-40 sliders are -10..+10 dB in half-decibel steps."""
    engine = _engine(_config("nova_7_perc_battery.yaml"))

    engine.send_eq_command([0, 20, 40] + [20] * 7)

    band_frame = engine.send_command.call_args_list[1].args[0]
    gains = [band_frame[3 + i * 6 + 3] for i in range(3)]
    # -10 dB → -100 decidecibels → 0x9C, 0 dB → 0x00, +10 dB → 100 → 0x64
    assert gains == [0x9C, 0x00, 0x64]


def test_unknown_format_is_refused_not_guessed(monkeypatch):
    config = _config("nova_7_perc_battery.yaml")
    object.__setattr__(config, "hardware_eq_format", "does_not_exist")
    engine = _engine(config)

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()
    assert engine.logger.error.called


def test_no_device_connected_is_not_an_error():
    engine = _engine(None)

    assert engine.send_eq_command([20] * 10) is False
    engine.send_command.assert_not_called()


def test_bands_are_appended_verbatim():
    """The 0-40 scale is ASM's own; nothing may rescale it on the way out."""
    engine = _engine(_config("nova_pro_wireless.yaml"))
    bands = [0, 5, 10, 15, 20, 25, 30, 35, 40, 20]

    engine.send_eq_command(bands)

    engine.send_command.assert_called_once_with([0x06, 0x33] + bands, 0)
