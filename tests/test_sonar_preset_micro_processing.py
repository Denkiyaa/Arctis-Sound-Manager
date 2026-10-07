# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sonar micro presets carry their capture processing (ClearCast, noise
reduction, gate, volume stabilizer); ASM used to read only their EQ."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arctis_sound_manager.gui import sonar_page as sp  # noqa: E402

PRESETS = Path(sp.__file__).parent / "presets"


def test_alias_boom_arm_turns_clearcast_on():
    data = json.loads((PRESETS / "Alias - Boom Arm [Mic].json").read_text())
    processing = sp._parse_preset_micro_processing(data)
    assert processing["noiseCanceling"] == {"enabled": True, "value": 0.926}
    assert processing["compressor"] == {"enabled": True, "value": 0.3}
    assert processing["noiseGate"] == {"enabled": False, "value": -60.0, "auto": False}


def test_clearcast_overrides_background_and_impact_reduction():
    data = {"noiseCancelingState": {"enabled": True, "value": 0.9},
            "noiseReductionState": {"enabled": True, "value": 0.5},
            "impactNoiseReductionState": {"enabled": True, "value": 0.5}}
    processing = sp._parse_preset_micro_processing(data)
    assert not processing["bgReduction"]["enabled"]
    assert not processing["impactReduction"]["enabled"]


def test_an_asm_preset_leaves_the_processing_alone():
    assert sp._parse_preset_micro_processing({"parametricEQ": {}, "macros": {}}) == {}


def test_out_of_range_and_non_finite_values_are_clamped():
    data = {"noiseGateState": {"enabled": True, "value": float("inf")},
            "volumeStabilizerState": {"enabled": True, "value": 7}}
    processing = sp._parse_preset_micro_processing(data)
    assert processing["noiseGate"]["value"] == -60.0
    assert processing["compressor"]["value"] == 1.0
