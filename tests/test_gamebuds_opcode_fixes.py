# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression guard for the GameBuds volume_limiter/wear_sense opcode swap,
plus coverage for the new anc_level/volume_bt/volume_24g controls.

base_arctis_gamebuds_dongle.device (SteelSeries' own spec) declares:
  0x27  volume_limiter (0=disabled/105dB max, 1=enabled/99dB max)
  0xc5  wear_sense      (0=disabled, 1=enabled)

gamebuds.yaml had these backwards — the "Volume Limiter" toggle sent to
0xc5 (real wear_sense) and "Wear Sense" sent to 0x27 (real volume_limiter),
so each control silently drove the other's feature.
"""

from pathlib import Path

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

DEVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _gamebuds() -> DeviceConfiguration:
    raw = _yaml.load((DEVICES_DIR / "gamebuds.yaml").read_text())
    return DeviceConfiguration(raw)


def _setting(config: DeviceConfiguration, section: str, name: str):
    for s in config.settings[section]:
        if s.name == name:
            return s
    raise AssertionError(f"{section}.{name} not found")


def test_volume_limiter_uses_the_real_volume_limiter_opcode():
    config = _gamebuds()
    setting = _setting(config, "headset", "volume_limiter")
    assert setting.update_sequence == [0x27, 'value']


def test_wear_sense_uses_the_real_wear_sense_opcode():
    config = _gamebuds()
    setting = _setting(config, "headset", "wear_sense")
    assert setting.update_sequence == [0xc5, 'value']


def test_device_init_pushes_volume_limiter_and_wear_sense_to_the_right_opcodes():
    config = _gamebuds()
    entries = {tuple(e) for e in config.device_init if len(e) == 2}
    assert (0x27, 'settings.volume_limiter') in entries
    assert (0xc5, 'settings.wear_sense') in entries
    # The old (swapped) pairing must not still be present anywhere.
    assert (0xc5, 'settings.volume_limiter') not in entries
    assert (0x27, 'settings.wear_sense') not in entries


def test_anc_level_is_independent_from_transparency_level():
    """0xb8 (ANC intensity) and 0xb9 (transparency intensity, "noise_level")
    are two separate memories on the device — must not collapse to one
    shared control."""
    config = _gamebuds()
    anc = _setting(config, "audio", "anc_level")
    transparency = _setting(config, "audio", "noise_level")
    assert anc.update_sequence == [0xb8, 'value']
    assert transparency.update_sequence == [0xb9, 'value']
    assert anc.min == 0x01 and anc.max == 0x03


def test_volume_bt_and_volume_24g_use_their_own_opcodes():
    config = _gamebuds()
    bt = _setting(config, "audio", "volume_bt")
    g24 = _setting(config, "audio", "volume_24g")
    assert bt.update_sequence == [0x23, 'value']
    assert g24.update_sequence == [0x25, 'value']
    assert bt.min == 0x00 and bt.max == 0x0f
    assert g24.min == 0x00 and g24.max == 0x0f
