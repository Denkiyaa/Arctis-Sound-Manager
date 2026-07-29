# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Issue #149 — Arctis Nova 3P / 3X Wireless ChatMix dial does nothing.

SteelSeries' own spec (base_arctis_nova_3_wireless_tx.device) makes ChatMix on
this dongle software-based: it is gated by `software_chatmix_status`
(command 0x49, status 0/1) and starts disabled. @rhavinx's usbhid-dump on
real hardware confirmed that with it left disabled, turning the dial produces
*zero* USB traffic on any interface — it behaves as a plain local volume
knob, so there is nothing for ASM to read until the daemon enables it.

What ships here is the enable alone: device_init sends [0x49, 0x01] (the
spec's `(enable-chatmix)` write), with no report_id prefix — this profile's
command_transport puts the opcode first (confirmed via translate_init_bytes /
send_command).

What deliberately does NOT ship is reading the two mix levels back from the
polled 0xB0 frame. The spec puts them there (chatmix_game / chatmix_chat), and
e830e66 removed them because polling those bytes while ChatMix was disabled
pinned Sonar's Game channel to ~3 % on init. Enabling the mode should make them
real, but a write the headset rejects is swallowed in silence — so until
@rhavinx confirms the dial moves, putting them back would restore that volume
bug for every owner of this headset on nothing but an inference.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _load_config() -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / "nova_3_wireless.yaml"))


def test_device_init_enables_software_chatmix():
    """device_init must contain the raw [0x49, 0x01] write (enable-chatmix).

    Not a 'settings.*' token: ChatMix-enabled is a mode this profile always
    wants on, not a user-adjustable value, so a literal pair is correct here
    (unlike e.g. mic_volume, which must come from settings./the saved value).
    """
    cfg = _load_config()

    assert cfg.device_init is not None
    assert [0x49, 0x01] in cfg.device_init


def test_translate_init_bytes_sends_chatmix_enable_with_no_report_id_prefix():
    """The command bytes that actually go over the wire for the ChatMix-enable
    step must be exactly [0x49, 0x01] — no report_id=0x00 prefix. This
    profile's command_transport (ctrl_output) carries the report id in
    ctrl_transfer's wValue, not as a leading data byte, so translate_init_bytes
    must hand back the opcode first, matching every other entry in this
    profile's device_init (e.g. [0xa3, 'settings.pm_shutdown']).
    """
    import threading
    from unittest.mock import MagicMock

    from arctis_sound_manager.core import CoreEngine
    from arctis_sound_manager.settings import DeviceSettings

    cfg = _load_config()
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.device_settings = DeviceSettings(cfg.vendor_id, cfg.product_ids[0])
    engine._setting_default = lambda name: CoreEngine._setting_default(engine, name)

    seq = CoreEngine.translate_init_bytes(engine, [0x49, 0x01])

    assert seq == [0x49, 0x01]


def test_polled_status_does_not_read_the_mix_bytes_yet():
    """0xB0 (headset_status) offsets, spec vs. this profile's response_mapping
    (indexed without the report id, per config.py's own docstring):

        field              spec offset   yaml offset (spec - 1)
        connection_status       2             0x01   (validated by capture)
        unused                  3             --     (deliberately unread)
        battery_status          4             0x03   (validated by capture)
        charging_status         5             0x04   (validated by capture)
        chatmix_game            6             0x05   (NOT read — see below)
        chatmix_chat            7             0x06   (NOT read — see below)

    The -1 shift is not in doubt; the mix bytes' *contents* are. Reading them
    is what pinned Sonar's Game channel to ~3 % (e830e66), and nothing yet
    proves [0x49, 0x01] was accepted — the headset cannot report a rejected
    write. This test fails the day someone re-adds them without that evidence.
    """
    cfg = _load_config()

    b0_mapping = next(
        m for m in cfg.status.response_mapping if m.starts_with == 0xB0
    )

    # Byte layout (index : meaning), one byte shorter than the spec struct
    # because report_id is not part of the frame ASM reads on this transport:
    #   0: command (0xB0)        1: connection_status (3 = paired/connected)
    #   2: unused                3: battery_status (67 %)
    #   4: charging_status (3=discharging)
    #   5: chatmix_game (72)     6: chatmix_chat (55)
    raw_frame = [0xB0, 0x03, 0xFF, 67, 0x03, 72, 55]

    values = b0_mapping.get_status_values(raw_frame)

    assert values["headset_power_status"] == 0x03
    assert values["headset_battery_charge"] == 67
    assert values["cable_charging"] == 0x03
    # Byte 0x02 is padding per the spec ("unused") — must never be surfaced
    # as a status variable (see CLAUDE.md: "a status variable pointing at a
    # padding byte displays plausible garbage").
    assert set(values.keys()) == {
        "headset_power_status", "headset_battery_charge", "cable_charging",
    }


def test_the_dial_still_reads_from_the_push_frame():
    """With 0xB0 left alone, the 0x45 push is the only path to the mix levels.

    It was never observed in the capture — the dial was silent because ChatMix
    was disabled — so whether it fires once the mode is on is exactly what the
    reporter's next test tells us.
    """
    cfg = _load_config()

    push = next(m for m in cfg.status.response_mapping if m.starts_with == 0x45)
    values = push.get_status_values([0x45, 72, 55])

    assert values["media_mix"] == 72
    assert values["chat_mix"] == 55


def test_media_mix_and_chat_mix_are_percentage_status_vars():
    """The two ChatMix status variables must be declared with a status_parse
    entry (percentage, 0-100) — SteelSeries' spec gives chatmix_game and
    chatmix_chat as `(range 0 100)` uint8 fields — or the daemon crashes
    trying to format an undeclared variable.
    """
    from arctis_sound_manager.config import StatusParseType

    cfg = _load_config()

    for var in ("media_mix", "chat_mix"):
        assert var in cfg.status_parse, f"{var} missing a status_parse entry"
        parsed = cfg.status_parse[var]
        assert parsed.type == StatusParseType.PERCENTAGE
        assert parsed.init_kwargs == {"perc_min": 0, "perc_max": 100}


def test_the_mix_is_wired_to_pipewire_not_to_the_device_page():
    """`representation` drives the device page; the mixer does not read it.

    manage_mix_change() takes device_status['media_mix'] / ['chat_mix']
    directly, so the dial moves the Game/Chat channels whether or not the two
    variables are listed here. Listing them would only add two read-only rows
    to the settings page — which is why their absence is not a missing piece
    of this fix.
    """
    cfg = _load_config()

    assert "chat_mix" not in cfg.status.representation["headset"]
    assert "media_mix" not in cfg.status.representation["headset"]


def test_device_init_asks_for_the_microphone_state():
    """Without this the Microphone section is missing until someone hits mute.

    The daemon starts with an empty status dict, get_status() drops a category
    whose variables are all unpopulated, and on this dongle nothing carries the
    mic state except the 0x52 push — which only fires on a change. 0x52 is a
    read command too (`(struct mic_status)` declares the outgoing form), and
    its reply has the same shape as the push, so the existing mapping parses it.
    """
    cfg = _load_config()

    assert [0x52] in cfg.device_init


def test_mic_status_maps_the_unknown_state():
    """255 is a documented value for this field and the readback can return it.

    Unmapped, it parses to nothing — and a category with no populated variable
    is removed wholesale, so the section would vanish exactly when the state is
    merely unknown.
    """
    cfg = _load_config()

    assert cfg.status_parse["mic_status"].init_kwargs["values"][0xFF] == "unknown"
