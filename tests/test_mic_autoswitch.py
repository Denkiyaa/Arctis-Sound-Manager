# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the auto mic-switch decision (resolve_mic_autoswitch_target).

The switch flips the Sonar Micro EQ input between the headset mic (__auto__) and
a configured alternate mic, on one of three triggers: off (manual only),
connection (headset power/online), or mute (headset mic mute state).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager.core import resolve_mic_autoswitch_target as R

ALT = "alsa_input.usb-Desktop_Mic-00.mono-fallback"


def test_off_never_switches():
    assert R("off", "headset_power_status", "headset_power_status", True, False, ALT) is None


def test_no_alternate_configured_never_switches():
    assert R("connection", "headset_power_status", "headset_power_status", False, False, "") is None


def test_connection_offline_switches_to_alt():
    assert R("connection", "headset_power_status", "headset_power_status", False, False, ALT) == ALT


def test_connection_online_switches_to_headset():
    assert R("connection", "headset_power_status", "headset_power_status", True, False, ALT) == "__auto__"


def test_connection_ignores_other_keys():
    assert R("connection", "mic_status", "headset_power_status", False, True, ALT) is None


def test_connection_headset_without_online_status_is_inert():
    # online_var None → the trigger can't fire (always-on wired headset).
    assert R("connection", "headset_power_status", None, True, False, ALT) is None


def test_mute_muted_switches_to_alt():
    assert R("mute", "mic_status", "headset_power_status", True, True, ALT) == ALT


def test_mute_unmuted_switches_to_headset():
    assert R("mute", "mic_status", "headset_power_status", True, False, ALT) == "__auto__"


def test_mute_ignores_other_keys():
    assert R("mute", "headset_power_status", "headset_power_status", False, True, ALT) is None


# ── mode "both": either condition engages the alternate (community request) ──

def test_both_offline_switches_to_alt_regardless_of_mute():
    assert R("both", "headset_power_status", "headset_power_status", False, False, ALT) == ALT


def test_both_online_and_unmuted_switches_to_headset():
    assert R("both", "headset_power_status", "headset_power_status", True, False, ALT) == "__auto__"


def test_both_online_but_muted_switches_to_alt():
    assert R("both", "mic_status", "headset_power_status", True, True, ALT) == ALT


def test_both_offline_via_mic_key_still_switches_to_alt():
    # Either status key can carry the trigger in "both" mode.
    assert R("both", "mic_status", "headset_power_status", False, False, ALT) == ALT


def test_both_ignores_unrelated_keys():
    assert R("both", "eq_band_value", "headset_power_status", False, True, ALT) is None
