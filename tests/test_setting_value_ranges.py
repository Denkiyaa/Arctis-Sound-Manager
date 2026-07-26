# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_setting_value_ranges.py — never offer a value the firmware rejects.

A control whose range is wider than what the hardware accepts looks fine in the
UI and does nothing beyond a certain point: the extra positions send bytes the
firmware has no meaning for.

The ranges below are the values that go out ON THE WIRE, transcribed from
SteelSeries' own device specifications (the `.edevice` descriptors shipped with
GG). That distinction matters and is easy to get backwards: several commands
declare a struct range that describes GG's *internal API* scale, then run the
value through a transform before sending it. The Nova 5's mic LED brightness
declares `range 0 3` but puts 0 / 1 / 4 / 10 on the wire; the GameBuds sidetone
declares `range 0 3` and sends 0 / 3 / 6 / 10. Reading the struct range alone
and "fixing" a profile to match it breaks a working control.

Only commands whose api-write passes the payload through untouched can have
their struct range trusted directly. Those are the ones listed here.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# (profile, setting) → (min, max) that the device accepts on the wire.
# Restricted to commands GG sends verbatim, with no UI→firmware transform.
FIRMWARE_RANGES: dict[tuple[str, str], tuple[int, int]] = {
    ("nova_5.yaml", "mic_side_tone"): (0, 10),
    ("nova_7_perc_battery.yaml", "mic_side_tone"): (0, 3),
    ("nova_7_perc_battery.yaml", "mic_volume"): (0, 7),
    ("nova_7_perc_battery.yaml", "mic_mute_led_brightness"): (0, 3),
    ("nova_7p_perc_battery.yaml", "mic_mute_led_brightness"): (0, 3),
    ("nova_pro_omni.yaml", "mic_volume"): (1, 10),
    ("nova_pro_omni.yaml", "mic_side_tone"): (1, 10),
    ("nova_pro_omni.yaml", "anc_level"): (1, 3),
    ("nova_pro_omni.yaml", "mic_led_brightness"): (0, 10),
}

# Controls whose wire values come from a transform table rather than a plain
# range. Listed so a future audit doesn't "correct" them into the struct range.
TRANSFORMED_CONTROLS: dict[tuple[str, str], set[int]] = {
    ("nova_5.yaml", "mic_mute_led_brightness"): {0x00, 0x01, 0x04, 0x0a},
    ("gamebuds.yaml", "mic_side_tone"): {0x00, 0x03, 0x06, 0x0a},
}


def _values_a_setting_can_send(cfg: dict) -> list[int]:
    """Every byte this control is able to put on the wire."""
    if cfg.get("values_mapping"):
        return [int(k) for k in cfg["values_mapping"]]
    if cfg.get("type") == "slider":
        low, high = int(cfg["min"]), int(cfg["max"])
        step = int(cfg.get("step", 1)) or 1
        return list(range(low, high + 1, step))
    if cfg.get("values"):
        return [int(v) for v in cfg["values"].values() if isinstance(v, int)]
    return []


def _load_setting(profile: str, setting: str) -> dict | None:
    device = YAML(typ="safe").load(DEVICES / profile)["device"]
    for section in (device.get("settings") or {}).values():
        if setting in section:
            return section[setting]
    return None


@pytest.mark.parametrize("key,bounds", sorted(FIRMWARE_RANGES.items()),
                         ids=[f"{p}:{s}" for p, s in sorted(FIRMWARE_RANGES)])
def test_setting_stays_within_firmware_range(key, bounds):
    profile, setting = key
    cfg = _load_setting(profile, setting)
    assert cfg is not None, f"{profile}: setting '{setting}' is gone"

    low, high = bounds
    out_of_range = [v for v in _values_a_setting_can_send(cfg) if not low <= v <= high]
    assert not out_of_range, (
        f"{profile}:{setting} can send {[hex(v) for v in out_of_range]}, "
        f"firmware accepts {low}-{high}")


@pytest.mark.parametrize("key,expected", sorted(TRANSFORMED_CONTROLS.items()),
                         ids=[f"{p}:{s}" for p, s in sorted(TRANSFORMED_CONTROLS)])
def test_transformed_control_keeps_its_wire_values(key, expected):
    """These send a fixed set of bytes — not the struct's declared range."""
    profile, setting = key
    cfg = _load_setting(profile, setting)
    assert cfg is not None, f"{profile}: setting '{setting}' is gone"
    assert set(_values_a_setting_can_send(cfg)) == expected, (
        f"{profile}:{setting} must send exactly {[hex(v) for v in sorted(expected)]} "
        f"— GG maps its UI scale onto these before writing")


@pytest.mark.parametrize("key,bounds", sorted(FIRMWARE_RANGES.items()),
                         ids=[f"{p}:{s}" for p, s in sorted(FIRMWARE_RANGES)])
def test_setting_default_is_reachable(key, bounds):
    """A default outside the range would be pushed at every device init."""
    profile, setting = key
    cfg = _load_setting(profile, setting)
    assert cfg is not None
    default = cfg.get("default")
    if default is None:
        return
    low, high = bounds
    assert low <= int(default) <= high, (
        f"{profile}:{setting} defaults to {hex(int(default))}, "
        f"outside firmware range {low}-{high}")
