# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_hardware_eq_encoding.py — the on-device parametric EQ wire format.

Every expectation here is a value SteelSeries' own device specification states
outright, so this stands in for hardware we don't have: the gain encoding
(0 dB = 0x00, 12 dB = 0x78, -12 dB = 0x88), the Q encoding (2.5 → 0x09C4 sent
low byte first), and the three-frame sequence the device needs before it
applies anything.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.hardware_eq import (CONNECTION_BLUETOOTH,
                                              CONNECTION_WIRELESS,
                                              DEFAULT_BAND_FREQUENCIES,
                                              FILTER_PEAK, EqBand,
                                              bands_from_gains, encode_band,
                                              encode_parametric_eq)


# ── Band encoding ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gain_db,expected", [
    (0.0, 0x00),
    (12.0, 0x78),    # spec: "12.0dB = 0x78"
    (-12.0, 0x88),   # spec: "-12.0dB = 0x88"
    (6.0, 0x3C),
    (-6.0, 0xC4),
    (0.5, 0x05),     # decidecibels, so half a dB is representable
])
def test_gain_is_signed_decidecibels(gain_db, expected):
    encoded = encode_band(EqBand(frequency=1000, gain_db=gain_db))
    assert encoded[3] == expected


@pytest.mark.parametrize("q,expected", [
    (2.5, bytes([0xC4, 0x09])),   # spec: "2.5 = 0x09C4", stored little-endian
    (1.5, bytes([0xDC, 0x05])),   # spec: "1.5 = 0x05DC"
    (0.8, bytes([0x20, 0x03])),   # spec: "0.8 = 0x0320"
])
def test_q_factor_is_thousandths_little_endian(q, expected):
    encoded = encode_band(EqBand(frequency=1000, gain_db=0, q=q))
    assert encoded[4:6] == expected


def test_band_is_six_bytes_in_order():
    encoded = encode_band(EqBand(frequency=1000, gain_db=0.0, q=1.41))
    assert len(encoded) == 6
    assert int.from_bytes(encoded[0:2], "little") == 1000
    assert encoded[2] == FILTER_PEAK


def test_values_are_clamped_to_what_the_field_holds():
    """A slider can't push the device outside its declared ranges."""
    encoded = encode_band(EqBand(frequency=99999, gain_db=99.0, q=99.0))
    assert int.from_bytes(encoded[0:2], "little") == 20000
    assert int.from_bytes(encoded[3:4], "little", signed=True) == 127
    assert int.from_bytes(encoded[4:6], "little") == 10000


# ── Frame sequence ───────────────────────────────────────────────────────────

def test_three_frames_in_the_order_the_device_expects():
    frames = encode_parametric_eq(bands_from_gains([0.0] * 10), name="Flat")

    assert len(frames) == 3
    assert frames[0][1] == 0xA7, "first frame carries the name"
    assert frames[1][1] == 0x33, "second frame carries the bands"
    assert frames[2][1] == 0x27, "third frame commits"


def test_name_frame_carries_connection_slot_and_ascii_name():
    frames = encode_parametric_eq(bands_from_gains([0.0] * 10), name="Flat")
    name_frame = frames[0]

    assert name_frame[2] == CONNECTION_WIRELESS
    assert name_frame[3] == 0x01  # custom preset slot
    assert bytes(name_frame[4:]) == b"Flat"


def test_band_frame_holds_ten_encoded_bands():
    frames = encode_parametric_eq(bands_from_gains([0.0] * 10))
    band_frame = frames[1]

    assert band_frame[2] == CONNECTION_WIRELESS
    assert len(band_frame) - 3 == 10 * 6


def test_gains_land_in_the_band_frame_in_order():
    gains = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    frames = encode_parametric_eq(bands_from_gains(gains))
    payload = frames[1][3:]

    for index, gain in enumerate(gains):
        assert payload[index * 6 + 3] == int(gain * 10)


def test_bluetooth_curve_targets_its_own_connection():
    frames = encode_parametric_eq(bands_from_gains([0.0] * 10),
                                  connection=CONNECTION_BLUETOOTH)
    assert frames[0][2] == CONNECTION_BLUETOOTH
    assert frames[1][2] == CONNECTION_BLUETOOTH


def test_default_frequencies_match_the_sliders():
    bands = bands_from_gains([0.0] * 10)
    assert tuple(b.frequency for b in bands) == DEFAULT_BAND_FREQUENCIES


def test_wrong_band_count_is_rejected():
    with pytest.raises(ValueError):
        encode_parametric_eq(bands_from_gains([0.0] * 9))


def test_non_ascii_name_does_not_break_the_frame():
    frames = encode_parametric_eq(bands_from_gains([0.0] * 10), name="Café ✨")
    assert all(0 <= b <= 255 for b in frames[0])
