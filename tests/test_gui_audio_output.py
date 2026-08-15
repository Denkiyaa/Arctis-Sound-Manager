# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the app's own playback comes out.

A Qt player left to itself plays to the system default, and here the system
default is the headset by design — it has to be, or its channels never see a
stream. So a user listening on Bluetooth earbuds got clip previews in a headset
on the desk, with nothing on screen to say why.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.audio_output import (device_id,
                                                   preferred_output_device)
from arctis_sound_manager.output_memory import OutputMemory

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.pro-output-0"
EARBUDS = "bluez_output.30_96_10_49_54_E2.1"


class _Device:
    """Stands in for QAudioDevice: an id as bytes is all this reads."""

    def __init__(self, name: str):
        self._name = name

    def id(self) -> bytes:
        return self._name.encode()


def _pick(order, available, default):
    return preferred_output_device(
        devices=[_Device(name) for name in available],
        default=_Device(default) if default else None,
        memory=OutputMemory(order),
    )


def test_the_remembered_earbuds_win_over_the_default_headset():
    chosen = _pick([EARBUDS], [HEADSET, EARBUDS], HEADSET)

    assert chosen is not None and device_id(chosen) == EARBUDS


def test_earbuds_in_their_case_leave_qt_alone():
    """Nothing remembered is plugged in, so the default is as good an answer as
    this has — and pinning it would freeze a choice Qt keeps current itself."""
    assert _pick([EARBUDS], [HEADSET], HEADSET) is None


def test_no_opinion_when_the_preference_is_already_the_default():
    assert _pick([HEADSET], [HEADSET, EARBUDS], HEADSET) is None


def test_nothing_to_play_out_of_is_not_an_error():
    assert _pick([EARBUDS], [], HEADSET) is None


def test_a_device_qt_cannot_name_does_not_take_the_choice():
    class _Unnameable:
        def id(self):
            raise RuntimeError("no id")

    chosen = preferred_output_device(
        devices=[_Unnameable(), _Device(EARBUDS)],
        default=_Device(HEADSET),
        memory=OutputMemory([EARBUDS]),
    )

    assert chosen is not None and device_id(chosen) == EARBUDS
