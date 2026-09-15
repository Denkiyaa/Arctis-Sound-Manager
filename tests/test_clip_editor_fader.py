# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The editor's channel faders: a squared taper, 0–100, preview == export.

Reported as "turning a channel down does nothing, only mute works". The fader
ran 0–150 and mapped straight to the player's linear volume: everything above
100 was clamped to 1.0 and did nothing, and 50 was −6 dB — audible as "a
little quieter", not as half. The taper below puts −12 dB at the middle.
"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.clip_editor import slider_gain, slider_position


def _db(gain: float) -> float:
    return 20 * math.log10(gain)


def test_the_middle_of_the_fader_is_twelve_db_down():
    assert _db(slider_gain(50)) == pytest.approx(-12.0, abs=0.1)


def test_the_ends_are_off_and_full():
    assert slider_gain(0) == 0.0
    assert slider_gain(100) == 1.0


def test_positions_above_the_range_do_not_exist():
    """Nothing above 1.0 — the player clamps there, so the old 100–150 was dead."""
    assert slider_gain(150) == 1.0


def test_position_and_gain_round_trip():
    for position in (0, 10, 25, 50, 71, 100):
        assert slider_position(slider_gain(position)) == position


def test_old_sidecar_values_land_where_they_sounded():
    """A sidecar written before the taper holds linear gains; 0.5 was −6 dB
    and must still be −6 dB after reopening."""
    assert _db(slider_gain(slider_position(0.5))) == pytest.approx(-6.0, abs=0.2)
    assert slider_position(1.0) == 100
    assert slider_position(1.5) == 100
