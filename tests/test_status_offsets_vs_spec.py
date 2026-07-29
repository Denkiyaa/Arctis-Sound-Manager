# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Findings from an audit of the device profiles against SteelSeries' own specs.

Two classes of defect, both invisible from inside the app — the headset never
complains, and the value it produces looks like an answer:

  1. A status variable pointing at a byte the spec declares `unused`. It shows
     padding, dressed as a connection state. Three profiles did this with
     `bluetooth_connection`.
  2. A PID filed under the wrong protocol family, so every byte is read one
     scale off. 0x22ab was read as a 0-4 battery when its own spec makes it a
     0-100 one.

The specs are not redistributable and live outside this repo, so these tests
lock the conclusions rather than re-deriving them — see the profiles' own
comments for the reasoning and the spec file and struct each rests on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _mapping(config: DeviceConfiguration, starts_with: int):
    return next(m for m in config.status.response_mapping
                if m.starts_with == starts_with)


# The `unused` byte in each family's headset_status struct, as the offset ASM
# would use for it (spec field index - 1, these buffers carrying no report id).
UNUSED_BYTE_OFFSET = {
    # base_arctis_nova_7_gen2_tx.device: unused sits between chat_chatmix_level
    # and bt_power_default.
    "nova_7_perc_battery.yaml": 0x06,
    "nova_7p_perc_battery.yaml": 0x06,
    # base_arctis_nova_5_tx.device: unused sits between connection_status and
    # battery_status.
    "nova_5.yaml": 0x02,
}


@pytest.mark.parametrize("profile,offset", sorted(UNUSED_BYTE_OFFSET.items()))
def test_no_status_variable_reads_the_spec_s_unused_byte(profile, offset):
    """Nothing may be read from a byte the spec declares as padding.

    All three profiles read it as `bluetooth_connection`, so the Bluetooth
    indicator reported a link state the frame never carried — and on the Nova
    5, which has no Bluetooth at all.
    """
    config = _config(profile)
    mapping = _mapping(config, 0xB0)

    offsets = {v: k for k, v in mapping.__dict__.items()
               if isinstance(v, int) and k != "starts_with"}

    assert offset not in offsets, (
        f"{profile} reads {offsets.get(offset)} from offset {offset:#04x}, "
        "which the spec declares unused"
    )


@pytest.mark.parametrize("profile", sorted(UNUSED_BYTE_OFFSET))
def test_bluetooth_connection_is_gone_from_those_profiles_entirely(profile):
    """A leftover status_parse entry or representation row would outlive the
    mapping and show up as an empty row rather than nothing at all."""
    config = _config(profile)

    assert "bluetooth_connection" not in config.status_parse
    for section in config.status.representation.values():
        assert "bluetooth_connection" not in section


def test_the_nova_5_has_no_empty_bluetooth_section_left():
    """Its only Bluetooth row was the phantom one — the section goes with it."""
    config = _config("nova_5.yaml")

    assert "bluetooth" not in config.status.representation


def test_wow_upgrade_pid_is_read_as_gen_2_not_gen_1():
    """0x22ab is the WoW dongle after its firmware update, not a hardware
    revision of 0x227a.

    `arctis_nova_7_wow_upgrade_tx.device` includes base_arctis_nova_7_gen2_tx
    (battery `range 1 100`) and resolves get-main-product-id to the Gen 2 id;
    0x227a — the same headset before the update — includes
    base_arctis_nova_7_tx instead. Filed under the Gen 1 profile, its battery
    byte was scaled against perc_max 4: a real 76 % came out as 1900 %, since
    percentage() clamps nothing without round_to.
    """
    gen1 = _config("nova_7_discrete_battery.yaml")
    gen2 = _config("nova_7_perc_battery.yaml")

    assert 0x22AB in gen2.product_ids
    assert 0x22AB not in gen1.product_ids
    # The pre-upgrade PID stays where it belongs.
    assert 0x227A in gen1.product_ids
    assert 0x227A not in gen2.product_ids


def test_the_two_nova_7_families_disagree_on_the_battery_scale():
    """The whole point of the PID move: these profiles read battery differently.

    If both ever declared the same scale, the move above would be cosmetic and
    this test would stop protecting anything.
    """
    gen1 = _config("nova_7_discrete_battery.yaml")
    gen2 = _config("nova_7_perc_battery.yaml")

    assert gen1.status_parse["headset_battery_charge"].init_kwargs["perc_max"] == 4
    assert gen2.status_parse["headset_battery_charge"].init_kwargs["perc_max"] == 100
