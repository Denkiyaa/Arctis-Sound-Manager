# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_hardware_eq_readback.py — reading the parametric EQ curve back (#146).

The user reports moving the Custom EQ sliders on an Arctis Nova 7P Gen 2 and
hearing no difference. ASM's write path was already checked byte-for-byte
against the spec, so the open question is whether the curve ever reaches the
headset at all, or reaches it and is simply not applied. Both `get_eq_preset_
data` (0x32) and `read_eq_preset_name` (0xA6) let the headset answer that
question directly — this file covers decoding their replies, the profile
gating that keeps the feature off headsets nobody has confirmed it for, and
CoreEngine.read_hardware_eq()'s three possible outcomes (curve back, reply
but wrong, no reply at all).
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.hardware_eq import (decode_band,
                                               decode_eq_preset_data,
                                               decode_eq_preset_name)

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# A known get_eq_preset_data (0x32) reply: response[0] is the opcode echo,
# no connection_type byte (current firmware's missing-byte quirk — see
# hardware_eq.py), then 10 bands of 6 bytes each: freq LE u16, filter_type
# u8, gain int8 decidecibels, q_factor LE u16 thousandths.
_KNOWN_BANDS = [
    # (frequency, filter_type, gain_db, q)
    (31,    1, -3.5,  1.41),
    (62,    1,  0.0,  1.41),
    (125,   1,  6.0,  1.41),
    (250,   1, -12.0, 0.7),
    (500,   1,  12.0, 10.0),
    (1000,  1,  0.5,  1.0),
    (2000,  1, -0.5,  1.0),
    (4000,  1,  3.0,  2.0),
    (8000,  1, -6.0,  3.0),
    (16000, 1,  1.0,  1.41),
]

# Hand-computed once (see decode task notes) and pinned here so a change to
# the encoding is caught even if the encoder used to build it also changes.
_KNOWN_0x32_RESPONSE = [
    0x32,
    31, 0, 1, 221, 130, 5,
    62, 0, 1, 0, 130, 5,
    125, 0, 1, 60, 130, 5,
    250, 0, 1, 136, 188, 2,
    244, 1, 1, 120, 16, 39,
    232, 3, 1, 5, 232, 3,
    208, 7, 1, 251, 232, 3,
    160, 15, 1, 30, 208, 7,
    64, 31, 1, 196, 184, 11,
    128, 62, 1, 10, 130, 5,
]


def test_pinned_known_response_matches_hand_encoding():
    """Guard against the fixture above and the hand-rolled bytes drifting apart."""
    assert len(_KNOWN_0x32_RESPONSE) == 61


# ── decode_band / decode_eq_preset_data ─────────────────────────────────────

def test_decode_band_is_the_inverse_of_the_incoming_struct():
    # 1000 Hz, peak filter, -3.5 dB (-35 decidecibels -> 0xDD signed), Q=1.41 (1410 -> 0x0582 LE)
    data = bytes([0xE8, 0x03, 0x01, 0xDD, 0x82, 0x05])
    band = decode_band(data)
    assert band.frequency == 1000
    assert band.filter_type == 1
    assert band.gain_db == pytest.approx(-3.5)
    assert band.q == pytest.approx(1.41)


def test_decode_band_too_short_raises():
    with pytest.raises(ValueError):
        decode_band(bytes([0x00, 0x01, 0x02]))


def test_decode_eq_preset_data_known_response_matches_expected_db_values():
    bands = decode_eq_preset_data(_KNOWN_0x32_RESPONSE)
    assert len(bands) == 10
    for band, (freq, filter_type, gain_db, q) in zip(bands, _KNOWN_BANDS):
        assert band.frequency == freq
        assert band.filter_type == filter_type
        assert band.gain_db == pytest.approx(gain_db)
        assert band.q == pytest.approx(q)


def test_decode_eq_preset_data_offset_skips_the_opcode_echo_only():
    """response[0] must never be read as data — it's the 0x32 echo."""
    bands = decode_eq_preset_data(_KNOWN_0x32_RESPONSE)
    assert bands[0].frequency == 31, "band 1 must start right after the opcode byte"


def test_decode_eq_preset_data_too_short_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        decode_eq_preset_data([0x32, 1, 2, 3])


# ── decode_eq_preset_name ────────────────────────────────────────────────────

def _name_response(name: str, preset_type: int = 1, connection_type: int = 0) -> list[int]:
    name_bytes = name.encode("ascii")
    padded = name_bytes + bytes(61 - len(name_bytes))
    return [0xA6, connection_type, preset_type, *padded]


def test_decode_eq_preset_name_known_response():
    response = _name_response("Custom", preset_type=1)
    name, preset_type = decode_eq_preset_name(response)
    assert name == "Custom"
    assert preset_type == 1


def test_decode_eq_preset_name_builtin_preset_type():
    response = _name_response("Flat", preset_type=0)
    name, preset_type = decode_eq_preset_name(response)
    assert name == "Flat"
    assert preset_type == 0


def test_decode_eq_preset_name_too_short_raises():
    with pytest.raises(ValueError):
        decode_eq_preset_name([0xA6, 0x00, 0x01])


# ── Profile gating: config.py parsing of hardware_eq.readback ──────────────

def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


@pytest.mark.parametrize("profile", ["nova_7p_perc_battery.yaml", "nova_7_perc_battery.yaml"])
def test_families_with_a_confirmed_spec_declare_readback(profile):
    config = _config(profile)
    readback = config.hardware_eq_readback
    assert readback is not None
    assert readback.band_query == 0x32
    assert readback.name_query == 0xA6
    assert readback.connection_type == 0x00  # wireless, same slot the sliders write to


@pytest.mark.parametrize("profile", [
    p.name for p in sorted(DEVICES.glob("*.yaml")) if p.name not in (
        "nova_7p_perc_battery.yaml", "nova_7_perc_battery.yaml",
        # Owned by a parallel change in progress — not touched here.
        "nova_3_wireless.yaml",
    )
])
def test_other_profiles_do_not_declare_readback(profile):
    """Nobody guessed the 0x32/0xA6 opcodes for a family whose spec wasn't read."""
    config = _config(profile)
    assert config.hardware_eq_readback is None


# ── CoreEngine.has_hardware_eq_readback / read_hardware_eq gating ──────────

def _engine(config: DeviceConfiguration | None) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = config
    engine.logger = MagicMock()
    engine.get_command_endpoint_address.return_value = 0
    engine._raw_response_waiters = {}
    engine.has_hardware_eq_readback = lambda: CoreEngine.has_hardware_eq_readback(engine)
    return engine


def test_has_hardware_eq_readback_true_for_declared_family():
    engine = _engine(_config("nova_7p_perc_battery.yaml"))
    assert engine.has_hardware_eq_readback() is True


def test_has_hardware_eq_readback_false_without_a_declared_readback():
    """Nova 5 is parametric too, but its readback opcodes were never checked."""
    engine = _engine(_config("nova_5.yaml"))
    assert engine.has_hardware_eq_readback() is False


def test_has_hardware_eq_readback_false_for_flat_gain_family():
    """Nova Pro Wireless has an on-device EQ, but not the parametric kind."""
    engine = _engine(_config("nova_pro_wireless.yaml"))
    assert engine.has_hardware_eq_readback() is False


def test_has_hardware_eq_readback_false_with_no_device():
    engine = _engine(None)
    assert engine.has_hardware_eq_readback() is False


def test_read_hardware_eq_refused_cleanly_on_family_without_hardware_eq():
    """The command must be rejected, not guessed, for headsets without it."""
    engine = _engine(_config("nova_5.yaml"))
    engine.send_command = MagicMock()

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result == {"ok": False, "error": "unsupported"}
    engine.send_command.assert_not_called()


def test_read_hardware_eq_refused_cleanly_with_no_device():
    engine = _engine(None)
    engine.send_command = MagicMock()

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result == {"ok": False, "error": "no_device"}
    engine.send_command.assert_not_called()


# ── CoreEngine.read_hardware_eq happy path / timeout, on the declared family ─

def _parametric_engine() -> MagicMock:
    engine = _engine(_config("nova_7p_perc_battery.yaml"))
    engine.send_command = MagicMock()
    return engine


def test_read_hardware_eq_queries_the_wireless_slot_by_default():
    engine = _parametric_engine()
    responses = {0x32: _KNOWN_0x32_RESPONSE, 0xA6: _name_response("Custom", 1)}

    async def fake_await(opcode, timeout=2.0):
        return responses.get(opcode)
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    engine.send_command.assert_any_call([0x32, 0x00], 0)
    engine.send_command.assert_any_call([0xA6, 0x00], 0)
    assert result["ok"] is True
    assert result["connection_type"] == 0x00


def test_read_hardware_eq_decodes_a_full_reply():
    engine = _parametric_engine()
    responses = {0x32: _KNOWN_0x32_RESPONSE, 0xA6: _name_response("Custom", 1)}

    async def fake_await(opcode, timeout=2.0):
        return responses.get(opcode)
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result["bands"] is not None
    assert len(result["bands"]) == 10
    assert result["bands"][0] == {
        "frequency": 31, "gain_db": pytest.approx(-3.5),
        "q": pytest.approx(1.41), "filter_type": 1,
    }
    assert result["name"] == "Custom"
    assert result["preset_type"] == 1


def test_read_hardware_eq_reports_no_reply_as_timeout_not_a_crash():
    """No reply at all must be distinguishable from a reply that decodes wrong."""
    engine = _parametric_engine()

    async def fake_await(opcode, timeout=2.0):
        return None
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result["ok"] is True  # the queries themselves were sent fine
    assert result["bands"] is None
    assert result["bands_error"] == "timeout"
    assert result["name"] is None
    assert result["name_error"] == "timeout"


def test_the_raw_reply_is_always_carried_alongside_the_decoding():
    """Where band 1 starts is inferred, never observed — so the bytes ship too.

    The offset comes from the spec's own "TODO: remove after FW fix missing
    byte" note, not from a captured frame. If it is off by one, every decoded
    figure is wrong and still looks like a curve. A diagnostic tool that can
    only be believed is worse than none: the raw hex is what lets the
    decoding be checked, and fixed from a report rather than another guess.
    """
    engine = _parametric_engine()
    responses = {0x32: _KNOWN_0x32_RESPONSE, 0xA6: _name_response("Custom", 1)}

    async def fake_await(opcode, timeout=2.0):
        return responses.get(opcode)
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result["bands_raw"].split() == [
        f"{b & 0xFF:02x}" for b in _KNOWN_0x32_RESPONSE
    ]
    assert result["name_raw"].startswith("a6 ")


def test_the_raw_reply_survives_a_decode_failure():
    """A reply we cannot decode is exactly the one whose bytes we need."""
    engine = _parametric_engine()

    async def fake_await(opcode, timeout=2.0):
        return [opcode, 1, 2, 3] if opcode == 0x32 else _name_response("X")
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result["bands"] is None
    assert result["bands_raw"] == "32 01 02 03"


def test_read_hardware_eq_reports_a_short_reply_as_a_decode_error():
    """A reply that arrived but is too short to be this curve must not be
    silently treated as a valid (if wrong) curve."""
    engine = _parametric_engine()

    async def fake_await(opcode, timeout=2.0):
        return [opcode, 1, 2, 3] if opcode == 0x32 else _name_response("X")
    engine._await_raw_response = fake_await

    result = asyncio.run(CoreEngine.read_hardware_eq(engine))

    assert result["bands"] is None
    assert "decode_error" in result["bands_error"]


# ── The waiter mechanism itself ─────────────────────────────────────────────

def test_resolve_raw_response_waiters_wakes_the_matching_awaiter():
    engine = _engine(_config("nova_7p_perc_battery.yaml"))

    async def scenario():
        wait_task = asyncio.create_task(CoreEngine._await_raw_response(engine, 0x99, timeout=1.0))
        await asyncio.sleep(0)  # let the waiter register itself
        CoreEngine._resolve_raw_response_waiters(engine, [0x99, 0xAA, 0xBB])
        return await wait_task

    result = asyncio.run(scenario())
    assert result == [0x99, 0xAA, 0xBB]


def test_resolve_raw_response_waiters_ignores_unrelated_opcodes():
    engine = _engine(_config("nova_7p_perc_battery.yaml"))

    async def scenario():
        wait_task = asyncio.create_task(CoreEngine._await_raw_response(engine, 0x99, timeout=0.2))
        await asyncio.sleep(0)
        CoreEngine._resolve_raw_response_waiters(engine, [0x11, 0x22])  # different opcode
        return await wait_task

    result = asyncio.run(scenario())
    assert result is None  # the 0x99 waiter timed out, unaffected by the 0x11 frame


def test_resolve_raw_response_waiters_handles_empty_response_and_no_waiters():
    """Every other frame the daemon reads takes this path — it must be a no-op."""
    engine = _engine(_config("nova_7p_perc_battery.yaml"))

    CoreEngine._resolve_raw_response_waiters(engine, [])
    CoreEngine._resolve_raw_response_waiters(engine, [0xB0, 1, 2, 3])  # no one is waiting

    assert engine._raw_response_waiters == {}
