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
                         report_id: int = 0x00,
                         name_command: int = 0xA7,
                         band_command: int = 0x33,
                         commit_command: int | None = None,
                         bands_carry_connection: bool = True) -> list[list[int]]:
    """Frames for the parametric families (Nova 7 Gen 2: 0xA7, Nova 5: 0xA5).

    GG sends the curve in order:

      1. `name_command` — connection, preset slot, then the preset name, which
         the headset reports back and shows on a DAC screen;
      2. `band_command` — the ten encoded bands;
      3. `commit_command` — apply. The Nova 7 Gen 2 needs this third write and
         changes nothing before it; the Nova 5 declares no such opcode and
         applies on the second. It therefore defaults to absent: a profile that
         needs the commit says so, and a profile that doesn't can't grow a
         stray frame by omission.

    The band frame differs between families, and getting it wrong shifts every
    byte of the curve: the Nova 7 Gen 2 repeats the connection there, the
    Nova 5 and the GameBuds do not. Hence `bands_carry_connection`.

    Returned as byte lists ready for CoreEngine.send_command(), which pads
    each one to the profile's frame length.
    """
    if len(bands) != 10:
        raise ValueError(f"expected 10 bands, got {len(bands)}")

    name_bytes = list(name.encode("ascii", errors="ignore")[:32])
    payload: list[int] = []
    for band in bands:
        payload += list(encode_band(band))

    band_frame = [report_id, band_command]
    if bands_carry_connection:
        band_frame.append(connection)

    frames = [
        [report_id, name_command, connection, preset_type] + name_bytes,
        band_frame + payload,
    ]
    if commit_command is not None:
        frames.append([report_id, commit_command])
    return frames


#: Encoders a device profile can name in `hardware_eq.format`.
ENCODERS = {
    "parametric": encode_parametric_eq,
}


# ── Reading the curve back (#146 diagnostics) ───────────────────────────────
#
# The write side above only proves ASM *sent* a conformant frame — it says
# nothing about what actually landed inside the headset. The Nova 7 Gen 2
# family's spec (base_arctis_nova_7_gen2_tx.device) declares two read-back
# commands: `get_eq_preset_data` (0x32) returns the ten stored bands,
# `read_eq_preset_name` (0xA6) returns the preset name and slot. Decoding
# their replies lets a user confirm whether the curve they set with the
# sliders is actually sitting in the headset.
#
# Two offset conventions collide here and both are spelled out because
# getting either wrong silently decodes padding as a plausible-looking value:
#
# 1. ASM's read buffers never carry the leading `report_id` byte the spec
#    declares as a constant field — this holds for every existing
#    response_mapping / SettingsReadback in this codebase (see config.py's
#    SettingsReadback docstring and the 0x20/0xA0/0xB0 replies these profiles
#    already parse: response[0] is the opcode echo, not report_id).
# 2. `get_eq_preset_data`'s incoming struct additionally carries
#    "; TODO: remove after FW fix missing byte" directly on its
#    connection_type field — the one field this struct has that none of its
#    siblings (read_eq_preset_name, audio_settings, ux_settings,
#    headset_status) needed a note for, all of which otherwise mirror their
#    own outgoing shape exactly. That singles out connection_type as the
#    byte current firmware omits: the reply is [opcode, band_1..band_10],
#    with no connection_type echo at all. `read_eq_preset_name` carries no
#    such note, so it keeps its full declared shape.

def decode_band(data: bytes) -> EqBand:
    """Six bytes → EqBand, the inverse of :func:`encode_band`.

    The *incoming* eqBand struct uses narrower types than the outgoing one:
    gain is a plain signed byte (not the float32 encode_band's caller
    produces) and Q is a 16-bit unsigned value of thousandths (not float32).
    Decoding with the outgoing widths would misread every band.
    """
    if len(data) < 6:
        raise ValueError(f"eq band frame too short: {len(data)} bytes (need 6)")
    frequency = int.from_bytes(data[0:2], "little")
    filter_type = data[2]
    gain_raw = int.from_bytes(data[3:4], "little", signed=True)
    q_raw = int.from_bytes(data[4:6], "little")
    return EqBand(
        frequency=frequency,
        gain_db=gain_raw / 10,
        q=q_raw / 1000,
        filter_type=filter_type,
    )


# Offset of band 1 inside a decoded get_eq_preset_data (0x32) reply, and the
# size of each band. response[0] is the 0x32 echo; per the missing-byte note
# above, connection_type is not echoed by current firmware, so band data
# starts right after the opcode. If a firmware update starts sending
# connection_type again, this becomes 2 and get_eq_preset_data replies grow
# by one byte — decode_eq_preset_data() will raise ValueError (reply too
# short for BAND_START=1 with the old expectation) rather than silently
# reading the curve one byte out of phase.
_EQ_BAND_START = 1
_EQ_BAND_SIZE = 6
_EQ_BAND_COUNT = 10


def decode_eq_preset_data(response: list[int]) -> list[EqBand]:
    """Decode a `get_eq_preset_data` (0x32) reply into its ten EqBand.

    Raises ValueError if the reply is shorter than expected — deliberately,
    rather than returning bands built from padding. See the module-level note
    above for the offset this assumes and why.
    """
    needed = _EQ_BAND_START + _EQ_BAND_SIZE * _EQ_BAND_COUNT
    if len(response) < needed:
        raise ValueError(
            f"get_eq_preset_data reply too short: got {len(response)} bytes, "
            f"need {needed}."
        )
    data = bytes(b & 0xFF for b in response)
    return [
        decode_band(data[_EQ_BAND_START + i * _EQ_BAND_SIZE:
                          _EQ_BAND_START + (i + 1) * _EQ_BAND_SIZE])
        for i in range(_EQ_BAND_COUNT)
    ]


# read_eq_preset_name's incoming struct carries no missing-byte note, so it
# mirrors its outgoing shape in full once report_id is stripped:
# response[0] = 0xA6 echo, [1] = connection_type, [2] = preset_type,
# [3:64] = 61-byte name.
_NAME_CONNECTION_OFFSET = 1
_NAME_PRESET_TYPE_OFFSET = 2
_NAME_START = 3
_NAME_LENGTH = 61


def decode_eq_preset_name(response: list[int]) -> tuple[str, int]:
    """Decode a `read_eq_preset_name` (0xA6) reply into (name, preset_type)."""
    needed = _NAME_START + _NAME_LENGTH
    if len(response) < needed:
        raise ValueError(
            f"read_eq_preset_name reply too short: got {len(response)} bytes, "
            f"need {needed}."
        )
    preset_type = response[_NAME_PRESET_TYPE_OFFSET]
    name_bytes = bytes(b & 0xFF for b in response[_NAME_START:_NAME_START + _NAME_LENGTH])
    name = name_bytes.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return name, preset_type
