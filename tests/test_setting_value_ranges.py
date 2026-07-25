# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_setting_value_ranges.py — never offer a value the firmware rejects.

A control whose range is wider than what the hardware accepts looks fine in the
UI and does nothing beyond a certain point: the extra positions send bytes the
firmware has no meaning for. Several profiles drifted that way — a 0-3 mic LED
brightness exposed as 0/1/4/10, a 0-3 sidetone exposed as 0/3/6/10, a 1-3
transparency level exposed as a 0-10 slider.

The ranges below come from SteelSeries' own device specifications (the
`.edevice` descriptors shipped with GG), which declare each command's accepted
field range. They are transcribed here rather than parsed at test time: the
specs are not redistributable, and these values only change when SteelSeries
ships new firmware semantics.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# (profile, setting) → (min, max) accepted by the firmware.
FIRMWARE_RANGES: dict[tuple[str, str], tuple[int, int]] = {
    ("nova_5.yaml", "mic_side_tone"): (0, 10),
    ("nova_5.yaml", "mic_mute_led_brightness"): (0, 3),
    ("gamebuds.yaml", "mic_side_tone"): (0, 3),
    ("gamebuds.yaml", "noise_level"): (1, 3),
    ("nova_7_perc_battery.yaml", "mic_side_tone"): (0, 3),
    ("nova_7_perc_battery.yaml", "mic_volume"): (0, 7),
    ("nova_7_perc_battery.yaml", "mic_mute_led_brightness"): (0, 3),
    ("nova_7p_perc_battery.yaml", "mic_mute_led_brightness"): (0, 3),
    ("nova_pro_omni.yaml", "mic_volume"): (1, 10),
    ("nova_pro_omni.yaml", "mic_side_tone"): (1, 10),
    ("nova_pro_omni.yaml", "anc_level"): (1, 3),
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
