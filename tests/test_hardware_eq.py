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
# profile → (leading command bytes, firmware value meaning 0 dB)
EXPECTED_EQ_COMMANDS = {
    "nova_pro_wireless.yaml": ([0x06, 0x33], 20),
    "arctis_nova_pro_wired.yaml": ([0x06, 0x33], 20),
    # Plain gain bytes, no report id prefix, on ASM's own 0-40 scale.
    "nova_7_discrete_battery.yaml": ([0x33], 20),
    "nova_7p_discrete_battery.yaml": ([0x33], 20),
    # Same opcode, but a ±12 dB scale: 0 dB is 24 here.
    "nova_4.yaml": ([0x33], 24),
}

# Profiles whose EQ takes a parametric payload instead (see hardware_eq.py).
PARAMETRIC_PROFILES = {
    # profile → the opcodes its frames must carry, in order
    "nova_7_perc_battery.yaml": [0xA7, 0x33, 0x27],
    "nova_7p_perc_battery.yaml": [0xA7, 0x33, 0x27],
    # The Nova 5 declares no commit opcode: it applies on the band frame.
    "nova_5.yaml": [0xA5, 0x33],
    "gamebuds.yaml": [0x19, 0x33],
    "nova_3_wireless.yaml": [0xA5, 0x33],
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
    command, zero_at = expected
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True
    engine.send_command.assert_called_once_with(command + [zero_at] * 10, 0)
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


@pytest.mark.parametrize("profile,expected_opcodes",
                         sorted(PARAMETRIC_PROFILES.items()))
def test_parametric_profiles_send_their_own_frames(profile, expected_opcodes):
    """Each family's frame sequence comes from its own spec, not a default."""
    engine = _engine(_config(profile))

    assert engine.send_eq_command([20] * 10) is True
    assert engine.has_hardware_eq() is True
    opcodes = [call.args[0][1] for call in engine.send_command.call_args_list]
    assert opcodes == expected_opcodes


def test_nova_4_shifts_onto_its_own_zero():
    """This family calls 0 dB 24, not 20 — a flat curve must not sit 2 dB low."""
    engine = _engine(_config("nova_4.yaml"))

    engine.send_eq_command([20] * 10)

    frame = engine.send_command.call_args_list[0].args[0]
    assert frame == [0x33] + [24] * 10


def test_families_on_asm_scale_are_not_shifted():
    engine = _engine(_config("nova_7_discrete_battery.yaml"))

    engine.send_eq_command([20] * 10)

    assert engine.send_command.call_args_list[0].args[0] == [0x33] + [20] * 10


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


def test_families_needing_a_pause_get_one_between_frames(monkeypatch):
    """GG raises its inter-command delay to 600 ms for these before the bands."""
    import arctis_sound_manager.core as core_mod

    slept: list[float] = []
    monkeypatch.setattr(core_mod.time, "sleep", slept.append)

    engine = _engine(_config("nova_5.yaml"))
    engine.send_eq_command([20] * 10)

    assert slept == [0.6], "the band frame must not follow the name immediately"


def test_families_without_a_declared_pause_do_not_wait(monkeypatch):
    import arctis_sound_manager.core as core_mod

    slept: list[float] = []
    monkeypatch.setattr(core_mod.time, "sleep", slept.append)

    engine = _engine(_config("nova_7_perc_battery.yaml"))
    engine.send_eq_command([20] * 10)

    assert slept == []
