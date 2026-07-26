# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Encoders for the equaliser that lives inside the headset.

Some Arctis families take a flat run of gain bytes (Nova Pro Wireless: report
id, opcode, then one byte per band). Others take a full parametric description
— frequency, filter type, gain and Q per band, plus a preset name — split over
several HID frames. That cannot be expressed in a device profile's
`update_sequence`, so a profile names an encoder here instead and the daemon
calls it.

Everything below is transcribed from SteelSeries' own device specifications,
including the value encodings, which are unusual enough to be worth stating:
gain is a signed byte of decidecibels (0 dB → 0x00, +12 dB → 0x78, -12 dB →
0x88) and Q is a 16-bit value of thousandths.
"""
from __future__ import annotations

from dataclasses import dataclass

# Filter types, per the specs' eqBand.filter_type.
FILTER_NONE = 0x00
FILTER_PEAK = 0x01
FILTER_LOW_PASS = 0x02
FILTER_HIGH_PASS = 0x03
FILTER_LOW_SHELF = 0x04
FILTER_HIGH_SHELF = 0x05
FILTER_NOTCH = 0x06

# Connection the curve applies to (parametric_eq.connection_type, and its
# derived structs parametric_eq_bt / parametric_eq_mic).
CONNECTION_WIRELESS = 0x00
CONNECTION_BLUETOOTH = 0x01
CONNECTION_MIC = 0x02

PRESET_BUILTIN = 0x00
PRESET_CUSTOM = 0x01

# The ten fixed frequencies ASM's custom EQ sliders sit on.
DEFAULT_BAND_FREQUENCIES = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
# Q the specs use for a plain 10-band graphic curve.
DEFAULT_Q = 1.41


@dataclass(frozen=True)
class EqBand:
    """One parametric band, in the units a user thinks in."""
    frequency: int
    gain_db: float
    q: float = DEFAULT_Q
    filter_type: int = FILTER_PEAK


def encode_band(band: EqBand) -> bytes:
    """Six bytes: frequency, filter type, gain, Q.

    Gain is decidecibels in a signed byte — the specs spell out 0 dB = 0x00,
    12 dB = 0x78, -12 dB = 0x88 — and Q is thousandths in 16 bits, both
    little-endian like every other multi-byte field in these frames.
    """
    frequency = max(20, min(20000, int(round(band.frequency))))
    gain = max(-128, min(127, int(round(band.gain_db * 10))))
    q = max(200, min(10000, int(round(band.q * 1000))))
    return (
        frequency.to_bytes(2, "little")
        + bytes([band.filter_type & 0xFF])
        + gain.to_bytes(1, "little", signed=True)
        + q.to_bytes(2, "little")
    )


def bands_from_gains(gains_db: list[float],
                     frequencies: tuple[int, ...] = DEFAULT_BAND_FREQUENCIES,
                     q: float = DEFAULT_Q) -> list[EqBand]:
    """Turn ASM's ten graphic-EQ gains into parametric bands."""
    return [EqBand(frequency=f, gain_db=g, q=q)
            for f, g in zip(frequencies, gains_db)]


def encode_parametric_eq(bands: list[EqBand], name: str = "Custom",
                         connection: int = CONNECTION_WIRELESS,
                         preset_type: int = PRESET_CUSTOM,
                         report_id: int = 0x00) -> list[list[int]]:
    """Frames for the Nova 7 Gen 2 / 7P Gen 2 parametric EQ (opcode 0xA7).

    GG sends three writes in order, and the device only applies the curve on
    the third:

      1. 0xA7 — connection, preset slot, then the preset name, which is what
         the headset reports back and what shows on a DAC screen;
      2. 0x33 — the ten encoded bands;
      3. 0x27 — commit.

    Returned as byte lists ready for CoreEngine.send_command(), which pads
    each one to the profile's frame length.
    """
    if len(bands) != 10:
        raise ValueError(f"expected 10 bands, got {len(bands)}")

    name_bytes = list(name.encode("ascii", errors="ignore")[:32])
    payload: list[int] = []
    for band in bands:
        payload += list(encode_band(band))

    return [
        [report_id, 0xA7, connection, preset_type] + name_bytes,
        [report_id, 0x33, connection] + payload,
        [report_id, 0x27],
    ]


#: Encoders a device profile can name in `hardware_eq.format`.
ENCODERS = {
    "parametric_a7": encode_parametric_eq,
}
