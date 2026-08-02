# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_derived_sequence_tokens.py — commands carrying a byte derived from a setting.

Most settings put their value straight on the wire. A few don't: the Nova Pro
Omni's microphone noise reduction sends [0x3C, which_mics, boom_level,
on_ear_level], where the first byte says which microphones are being adjusted
(0 none / 1 boom / 16 on-ear / 17 both) and the levels must stay >= 1 even when
the feature is off — send a level of 0 and the firmware, per its own spec,
behaves unpredictably.

`value.enabled` and `value.at_least_1` let a profile express that without any
device-specific code in the daemon.
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


def _engine(profile: str) -> tuple[MagicMock, DeviceConfiguration]:
    config = DeviceConfiguration(YAML(typ="safe").load(DEVICES / profile))
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    return engine, config


def _setting(config: DeviceConfiguration, name: str):
    return next(s for section in config.settings.values()
                for s in section if s.name == name)


@pytest.mark.parametrize("value,expected", [
    (0, [0x01, 0x3C, 0x00, 0x01, 0x01]),  # off: no mic selected, levels pinned to 1
    (1, [0x01, 0x3C, 0x01, 0x01, 0x01]),  # boom only, low
    (2, [0x01, 0x3C, 0x01, 0x02, 0x01]),
    (3, [0x01, 0x3C, 0x01, 0x03, 0x01]),
])
def test_omni_noise_reduction_frames(value, expected):
    engine, config = _engine("nova_pro_omni.yaml")
    setting = _setting(config, "mic_noise_reduction")

    assert CoreEngine._resolve_update_sequence(engine, setting, value) == expected


def test_level_never_reaches_the_device_as_zero():
    """The spec pins levels to >= 1; 0 is reserved for the selector byte."""
    engine, config = _engine("nova_pro_omni.yaml")
    setting = _setting(config, "mic_noise_reduction")

    frame = CoreEngine._resolve_update_sequence(engine, setting, 0)

    assert frame[3] >= 1 and frame[4] >= 1


@pytest.mark.parametrize("value,expected", [
    (0,  [0x01, 0x38, 0x00, 0x01]),  # off: state 0, level pinned to 1
    (1,  [0x01, 0x38, 0x01, 0x01]),  # on, lowest
    (5,  [0x01, 0x38, 0x01, 0x05]),
    (10, [0x01, 0x38, 0x01, 0x0a]),  # on, highest
])
def test_omni_sidetone_frames(value, expected):
    """The boom sidetone splits into a state byte + a level byte, so 0 is a
    true off rather than a floor of 10% (#161)."""
    engine, config = _engine("nova_pro_omni.yaml")
    setting = _setting(config, "mic_side_tone")

    assert CoreEngine._resolve_update_sequence(engine, setting, value) == expected


def test_omni_sidetone_init_is_off_when_unset():
    """At init with no saved value the sidetone must resolve to state OFF —
    the regression in #161 was the init hardcoding the state byte to 1, so the
    sidetone came back on at every connect no matter what the user chose."""
    import threading
    from arctis_sound_manager.settings import DeviceSettings

    _, config = _engine("nova_pro_omni.yaml")
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.device_settings = DeviceSettings(config.vendor_id, config.product_ids[0])
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)

    resolved = CoreEngine.translate_init_bytes(
        engine, [0x01, 0x38, "settings.mic_side_tone.enabled",
                 "settings.mic_side_tone.at_least_1"])
    assert resolved == [0x01, 0x38, 0x00, 0x01]


def test_tokens_are_inert_for_ordinary_settings():
    """A plain setting must keep sending its value untouched."""
    engine, config = _engine("nova_pro_omni.yaml")
    setting = _setting(config, "mic_volume")

    assert CoreEngine._resolve_update_sequence(engine, setting, 7) == [0x01, 0x37, 7]


def test_unknown_token_is_rejected_loudly():
    """A typo in a profile must fail, not silently send a wrong byte."""
    engine, _ = _engine("nova_pro_omni.yaml")
    setting = MagicMock()
    setting.update_sequence = [0x01, 0x3C, "value.nonsense"]

    with pytest.raises(Exception):
        CoreEngine._resolve_update_sequence(engine, setting, 1)
