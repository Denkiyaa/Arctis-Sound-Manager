# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Sonar EQ curve applying itself.

Curve edits used to wait behind an Apply button, because applying one meant
restarting filter-chain and losing the audio for a few seconds — the button
was there so at least the user chose when to pay it. The band rack (Phase 4,
see sonar_to_pipewire._band_slot_rack) removed that cost, so the curve now
behaves like the macro sliders next to it: you move it, you hear it.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arctis_sound_manager.eq_types import EqBand  # noqa: E402
from arctis_sound_manager.gui import sonar_page as sp  # noqa: E402


@pytest.fixture
def channel(monkeypatch, tmp_path):
    """A Game channel widget whose applies are recorded instead of run."""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(sp, "_CFG", tmp_path)
    monkeypatch.setattr(sp, "_PRESETS_DIR", tmp_path / "sonar_presets")

    applied: list[list[EqBand]] = []
    monkeypatch.setattr(
        sp.SonarChannelWidget, "_do_apply",
        lambda self: applied.append(list(self._cur_bands)),
    )

    widget = sp.SonarChannelWidget("game")
    widget._preset_bands = [EqBand(freq=100, gain=1.0)]
    widget._cur_bands = list(widget._preset_bands)
    widget._eq_widget.set_bands(widget._preset_bands)
    widget._applied = applied
    return widget


def _fire(widget):
    """Run the debounce timer's payload now instead of waiting on it."""
    assert widget._apply_timer.isActive(), "the edit did not schedule an apply"
    widget._apply_timer.stop()
    widget._do_apply()


def test_a_curve_edit_applies_without_being_asked(channel):
    edited = [EqBand(freq=100, gain=1.0), EqBand(freq=900, gain=-3.0)]
    channel._eq_widget.bands_changed.emit(edited)
    _fire(channel)
    assert [(b.freq, b.gain) for b in channel._applied[-1]] == [(100, 1.0), (900, -3.0)]


def test_there_is_no_apply_button_left_to_press(channel):
    assert not hasattr(channel, "_apply_btn")


def test_an_edit_offers_a_way_back_to_the_preset(channel):
    channel._eq_widget.bands_changed.emit([EqBand(freq=100, gain=1.0),
                                           EqBand(freq=900, gain=-3.0)])
    assert channel._revert_btn.isVisibleTo(channel)

    channel._on_revert_eq()
    _fire(channel)
    assert [(b.freq, b.gain) for b in channel._applied[-1]] == [(100, 1.0)]
    assert not channel._revert_btn.isVisibleTo(channel)


def test_going_back_to_the_preset_curve_stops_offering_revert(channel):
    """Undoing the edit by hand is the same thing as reverting it."""
    channel._eq_widget.bands_changed.emit([EqBand(freq=100, gain=1.0),
                                           EqBand(freq=900, gain=-3.0)])
    channel._eq_widget.bands_changed.emit([EqBand(freq=100, gain=1.0)])
    assert not channel._revert_btn.isVisibleTo(channel)


def test_curve_edits_land_faster_than_a_macro_drag(channel):
    """Curve edits are one-shot (they arrive when the drag ends) and cost a
    few set-param calls, so they wait far less than the macro sliders, which
    stream values while being dragged."""
    channel._eq_widget.bands_changed.emit([EqBand(freq=100, gain=1.0),
                                           EqBand(freq=900, gain=-3.0)])
    assert channel._apply_timer.interval() == sp._BAND_APPLY_DELAY

    channel._schedule_apply()
    assert channel._apply_timer.interval() == sp._APPLY_DELAY
