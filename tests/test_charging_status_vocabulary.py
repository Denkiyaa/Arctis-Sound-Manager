# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_charging_status_vocabulary.py — cable_charging must cover all four states.

SteelSeries' own translate_charging_status is identical on every Arctis family:

    0 unknown / headset not connected
    1 plugged in, charging
    2 plugged in, not charging
    3 discharging (on battery)

Profiles used to map only 1 and 3, so the indicator silently disappeared once a
headset finished charging on its cable (state 2) — int_str_mapping returns None
for an unmapped value. Every state also needs an en.ini key or the UI shows the
raw identifier.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest
from ruamel.yaml import YAML

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
EN_INI = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "lang" / "en.ini"

EXPECTED_STATES = {0x00, 0x01, 0x02, 0x03}


def _profiles_with_cable_charging() -> list[tuple[str, dict]]:
    yaml = YAML(typ="safe")
    found = []
    for path in sorted(DEVICES.glob("*.yaml")):
        parse = yaml.load(path)["device"].get("status_parse") or {}
        if "cable_charging" in parse:
            found.append((path.name, parse["cable_charging"]))
    return found


def test_profiles_are_found():
    assert _profiles_with_cable_charging(), "no profile declares cable_charging"


@pytest.mark.parametrize("name,parse",
                         _profiles_with_cable_charging(),
                         ids=[n for n, _ in _profiles_with_cable_charging()])
def test_all_four_charging_states_are_mapped(name, parse):
    assert parse["type"] == "int_str_mapping", (
        f"{name}: on_off collapses three distinct states into 'off'")
    assert set(parse["values"]) == EXPECTED_STATES, (
        f"{name}: expected states {sorted(EXPECTED_STATES)}, "
        f"got {sorted(parse['values'])}")
    # Charging and on-battery keep the vocabulary the UI already used.
    assert parse["values"][0x01] == "on"
    assert parse["values"][0x03] == "off"


@pytest.mark.parametrize("name,parse",
                         _profiles_with_cable_charging(),
                         ids=[n for n, _ in _profiles_with_cable_charging()])
def test_charging_values_have_translations(name, parse):
    ini = configparser.ConfigParser()
    ini.read(EN_INI)
    for value in parse["values"].values():
        assert str(value) in ini["status_values"], (
            f"{name}: '{value}' missing from [status_values] in en.ini")
