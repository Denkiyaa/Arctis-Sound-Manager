# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_nova_pro_omni_protocol.py — Nova Pro Omni (0x2290) hardware-validated protocol.

Locks the facts that came out of the USBPcap capture / GG spec work (PR #147)
and, above all, the invariants ASM itself depends on:

  1. Commands go out as SET_REPORT with wValue 0x0201 (command_report_id = 1).
  2. headset_power_status still exists, is fed by the pairing byte, and speaks
     the on/off vocabulary power_status.py understands — the tray battery icon,
     the OLED and the routing fallback all read it.
  3. device_init never writes a hardcoded user-facing value: every payload byte
     is either a handshake/query or a 'settings.*' placeholder.
  4. The 0x01b0 status frame decodes to the values the DAC actually reports.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import (ConfigStatusResponseMapping,
                                         DeviceConfiguration, parsed_status)
from arctis_sound_manager.power_status import (HeadsetPower,
                                               normalize_power_value)

OMNI_YAML = (Path(__file__).parent.parent / "src" / "arctis_sound_manager"
             / "devices" / "nova_pro_omni.yaml")


@pytest.fixture(scope="module")
def raw() -> dict:
    return YAML(typ="safe").load(OMNI_YAML)["device"]


@pytest.fixture(scope="module")
def config(raw) -> DeviceConfiguration:
    return DeviceConfiguration({"device": raw})


def _status_frame(pairing: int = 0x08, charging: int = 0x08) -> list[int]:
    """A realistic 0x01b0 wireless_settings reply (headset on, on battery)."""
    frame = [0x00] * 64
    frame[0x00], frame[0x01] = 0x01, 0xb0
    frame[0x02] = 0x00  # bluetooth_powerup_state: off
    frame[0x03] = 0x00  # bluetooth_auto_mute: nothing
    frame[0x04] = 0x01  # bluetooth_power_status: off
    frame[0x05] = 0x01  # bluetooth_connection: ready
    frame[0x06] = 75    # headset battery %
    frame[0x07] = 50    # charge slot battery %
    frame[0x08] = 0x05  # transparency level
    frame[0x09] = 0x00  # mic unmuted
    frame[0x0a] = 0x02  # noise_cancelling: ANC on
    frame[0x0b] = 0x0a  # mic LED brightness
    frame[0x0c] = 0x05  # auto off: 30 minutes
    frame[0x0d] = 0x00  # wireless mode: speed
    frame[0x0e] = pairing
    frame[0x0f] = charging
    frame[0x10] = 0x03  # ANC level: high
    return frame


def _b0_mapping(raw) -> ConfigStatusResponseMapping:
    entry = next(m for m in raw["status"]["response_mapping"]
                 if m["starts_with"] == 0x01b0)
    return ConfigStatusResponseMapping(**entry)


def test_command_report_id_drives_wvalue(raw):
    """0x0201, not 0x0200 — the DAC ignores a mismatched report id."""
    assert raw["command_report_id"] == 0x01
    assert raw["command_transport"] == "ctrl_output"
    assert raw["oled"]["wvalue"] == 0x0301  # Feature report for draw_bitmap


def test_status_frame_decodes(raw, config):
    parsed = parsed_status(_b0_mapping(raw).get_status_values(_status_frame()), config)

    assert parsed["headset_battery_charge"] == 75
    assert parsed["charge_slot_battery_charge"] == 50
    assert parsed["wireless_pairing"] == "connected"
    assert parsed["charging_status"] == "discharging"
    assert parsed["anc_level"] == "high"
    assert parsed["noise_cancelling"] == "on"
    assert parsed["mic_status"] == "unmuted"
    assert parsed["auto_off_time_minutes"] == 30


@pytest.mark.parametrize("pairing,expected,power", [
    (0x08, "online", HeadsetPower.ON),    # radio link up
    (0x04, "offline", HeadsetPower.OFF),  # paired but headset off
    (0x02, "offline", HeadsetPower.OFF),  # searching
    (0x01, "offline", HeadsetPower.OFF),  # not paired
])
def test_headset_power_status_survives(raw, config, pairing, expected, power):
    """ASM-wide contract: headset_power_status exists and reads on/off.

    It used to be mapped onto byte 0x0f, which is really the charging state —
    hence a headset reported as 'cable_charging' while off. It now shares byte
    0x0e with wireless_pairing, in the vocabulary power_status.py normalizes.
    """
    parsed = parsed_status(
        _b0_mapping(raw).get_status_values(_status_frame(pairing=pairing)), config)

    assert parsed["headset_power_status"] == expected
    assert normalize_power_value(parsed["headset_power_status"]) is power


def test_online_status_points_at_power_status(raw):
    assert raw["online_status"]["status_variable"] == "headset_power_status"
    assert raw["online_status"]["online_value"] == "online"


def test_push_event_also_updates_power_status(raw):
    """The 0x07b5 radio/BT event must refresh power state, not just pairing."""
    entry = next(m for m in raw["status"]["response_mapping"]
                 if m["starts_with"] == 0x07b5)
    assert entry["headset_power_status"] == entry["wireless_pairing"]


def test_device_init_hardcodes_no_user_setting(raw):
    """Every init payload is a handshake/query or a 'settings.*' placeholder.

    GG replays fixed stream-mix, line-out and Bluetooth values at startup; ASM
    must not, or it silently overwrites what the user set on the DAC itself.
    """
    # Constants that are part of the wire format rather than user state:
    #   0x8d/0x49 — sonar-present / software-chatmix handshake flags,
    #   0x38      — boom_mic_sidetone takes state THEN level; the level is the
    #               setting, the leading state byte only says "sidetone on".
    structural = {0x8d: 1, 0x49: 1, 0x38: 1}
    for command in raw["device_init"]:
        if command == ["status.request"]:
            continue
        report_id, opcode, *payload = command
        assert report_id == 0x01, f"{command}: missing report id"
        for byte in payload[structural.get(opcode, 0):]:
            assert isinstance(byte, str) and byte.startswith("settings."), (
                f"opcode 0x{opcode:02x} writes a hardcoded value {byte!r}"
            )


def test_init_resolves_to_profile_defaults_with_no_saved_settings(raw, config):
    """A fresh install must send the declared defaults, never a stray 0.

    'gain' and 'wireless_mode' were dropped from this profile; nothing in the
    init sequence may still resolve through them.
    """
    import threading
    from unittest.mock import MagicMock

    from arctis_sound_manager.core import CoreEngine
    from arctis_sound_manager.settings import DeviceSettings

    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.device_settings = DeviceSettings(config.vendor_id, config.product_ids[0])
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)

    for command in raw["device_init"]:
        if command == ["status.request"]:
            continue
        resolved = CoreEngine.translate_init_bytes(engine, list(command))
        assert all(isinstance(b, int) for b in resolved), f"{command} → {resolved}"
        assert len(resolved) == len(command)

    # Spot-check the two settings this profile introduced.
    assert CoreEngine.translate_init_bytes(
        engine, [0x01, 0x27, "settings.volume_limiter"]) == [0x01, 0x27, 0x00]
    assert CoreEngine.translate_init_bytes(
        engine, [0x01, 0xb8, "settings.anc_level"]) == [0x01, 0xb8, 0x03]


def test_settings_reference_existing_entries(raw):
    """Every 'settings.x' used at init is actually declared as a setting."""
    declared = {name for section in raw["settings"].values() for name in section}
    for command in raw["device_init"]:
        for byte in command:
            if isinstance(byte, str) and byte.startswith("settings."):
                assert byte.split(".", 1)[1] in declared, f"{byte} is not declared"
