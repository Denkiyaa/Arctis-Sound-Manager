# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_settings_readback.py — adopt what the headset has, never override a choice.

ASM pushes its own settings at every device init. That is right once the user
has made a choice, and wrong before: a headset configured elsewhere — in GG on
a dual-boot machine, or from the device's own controls — was flattened to
profile defaults the first time ASM ran.

Reading the device's values back fixes that, but only for settings the user
has never touched. Once a setting is in their settings file it is theirs, and
no reply from the device may overwrite it — otherwise a readback racing a
change would undo it.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.settings import DeviceSettings

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
PROFILE = "nova_7_perc_battery.yaml"


def _engine(user_chosen: set[str] | None = None,
            current: dict[str, int] | None = None) -> MagicMock:
    config = DeviceConfiguration(YAML(typ="safe").load(DEVICES / PROFILE))
    settings = DeviceSettings(0x1038, 0x22A1)
    for name, value in (current or {}).items():
        settings.settings[name] = value
    for name in (user_chosen or set()):
        settings._user_chosen.add(name)

    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.device_settings = settings
    engine.logger = MagicMock()
    engine.get_command_endpoint_address.return_value = 0
    return engine


def test_queries_are_sent_at_init():
    engine = _engine()
    sent = []
    engine.send_command = lambda payload, ep: sent.append(payload)

    CoreEngine._request_settings_readback(engine)

    assert sent == [[0x20], [0xA0]]


def test_untouched_settings_adopt_the_device_value():
    engine = _engine(current={"mic_volume": 7, "mic_side_tone": 0})

    CoreEngine._absorb_settings_readback(engine, [0x20, 3, 2, 1])

    assert engine.device_settings.get("mic_volume") == 3
    assert engine.device_settings.get("mic_side_tone") == 2
    assert engine.device_settings.get("volume_limiter") == 1


def test_a_user_choice_is_never_overwritten():
    engine = _engine(user_chosen={"mic_volume"},
                     current={"mic_volume": 7, "mic_side_tone": 0})

    CoreEngine._absorb_settings_readback(engine, [0x20, 3, 2, 1])

    assert engine.device_settings.get("mic_volume") == 7, "user choice lost"
    assert engine.device_settings.get("mic_side_tone") == 2


def test_second_query_feeds_its_own_settings():
    engine = _engine(current={"pm_shutdown": 30, "mic_mute_led_brightness": 3})

    CoreEngine._absorb_settings_readback(engine, [0xA0, 15, 1])

    assert engine.device_settings.get("pm_shutdown") == 15
    assert engine.device_settings.get("mic_mute_led_brightness") == 1


def test_unrelated_frames_are_ignored():
    """A status frame must not be mistaken for a settings reply."""
    engine = _engine(current={"mic_volume": 7})

    CoreEngine._absorb_settings_readback(engine, [0xB0, 3, 64, 3, 0, 0, 0, 0, 0, 0])

    assert engine.device_settings.get("mic_volume") == 7


def test_short_reply_does_not_crash():
    engine = _engine(current={"mic_volume": 7})

    CoreEngine._absorb_settings_readback(engine, [0x20])

    assert engine.device_settings.get("mic_volume") == 7


def test_profiles_without_readback_do_nothing():
    config = DeviceConfiguration(YAML(typ="safe").load(DEVICES / "nova_5.yaml"))
    engine = _engine()
    engine.device_config = config
    engine.send_command = MagicMock()

    CoreEngine._request_settings_readback(engine)
    CoreEngine._absorb_settings_readback(engine, [0x20, 3, 2, 1])

    engine.send_command.assert_not_called()


def test_settings_file_marks_values_as_user_chosen(tmp_path, monkeypatch):
    """The distinction the whole feature rests on."""
    monkeypatch.setattr("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path)
    settings = DeviceSettings(0x1038, 0x22A1)
    settings.settings["mic_volume"] = 7
    settings.settings["mic_side_tone"] = 0
    (tmp_path / "1038_22a1.yaml").write_text("mic_volume: 5\n")

    settings.read_from_file()

    assert settings.was_chosen_by_user("mic_volume") is True
    assert settings.was_chosen_by_user("mic_side_tone") is False
    assert settings.get("mic_volume") == 5
