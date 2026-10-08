# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the size the clip editor opens at, and refuses to go below.

Reported from use: the dialog opened small enough that the preview was a strip,
and resizing it further ran the trim band's markers and read-out into the preset
buttons underneath. Both are size problems — one about what it opens at, one
about what it must never shrink past.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize

from arctis_sound_manager.gui.clip_editor import (MIN_SIZE, PREFERRED_SIZE,
                                                  _opening_size)


def test_it_opens_larger_than_the_old_fixed_minimum():
    """720x560 was both the minimum and, in practice, the opening size."""
    opened = _opening_size(QSize(2560, 1440))
    assert opened.width() > 720 and opened.height() > 560


def test_a_large_screen_gets_the_preferred_size_not_more():
    """Bigger is not better past this: the preview stops gaining from it."""
    assert _opening_size(QSize(3840, 2160)) == PREFERRED_SIZE


def test_it_never_opens_larger_than_the_screen():
    """A dialog wider than the display cannot be dragged back into view on
    some compositors."""
    screen = QSize(1366, 768)
    opened = _opening_size(screen)
    assert opened.width() <= screen.width()


def test_a_small_screen_still_gets_a_usable_dialog():
    """On a display smaller than the minimum, the minimum wins — a squeezed
    layout is the failure being fixed, not an acceptable fallback."""
    opened = _opening_size(QSize(1024, 600))
    assert opened.width() >= MIN_SIZE.width()
    assert opened.height() >= MIN_SIZE.height()


def test_the_minimum_fits_the_rows_that_share_a_line():
    """The trim band, five preset buttons and the span read-out are one row;
    so are the size picker and the two export buttons."""
    assert MIN_SIZE.width() >= 900
    assert MIN_SIZE.height() >= 640


def test_the_band_cannot_be_squeezed_under_its_own_markers():
    """The band paints handles, end times and a playhead; below this width they
    are drawn on top of each other."""
    from arctis_sound_manager.gui.trim_band import EDGE_PAD

    # A width the band is guaranteed by the dialog minimum, less the margins.
    assert MIN_SIZE.width() - 2 * 18 - 2 * EDGE_PAD >= 360


# ── size and frame-rate pickers ────────────────────────────────────────────────

def test_the_recorded_rate_is_offered_as_its_number_and_in_order():
    from arctis_sound_manager.gui.clip_editor import fps_options

    assert fps_options(23.1) == [("15 fps", 15), ("23 fps", None),
                                 ("30 fps", 30), ("60 fps", 60)]


def test_a_fixed_rate_next_to_the_recorded_one_is_not_offered():
    """30 beside a 29.6 fps clip is the same rate, re-encoded for nothing."""
    from arctis_sound_manager.gui.clip_editor import fps_options

    assert fps_options(29.6) == [("15 fps", 15), ("30 fps", None), ("60 fps", 60)]


def test_an_unknown_rate_still_has_a_free_default():
    from arctis_sound_manager.gui.clip_editor import fps_options

    options = fps_options(None)
    assert options[0][1] is None
    assert [v for _, v in options[1:]] == [15, 30, 60]


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_free_choice_starts_checked_and_a_click_moves_it(qapp):
    from arctis_sound_manager.gui.clip_editor import _Choices, fps_options

    picker = _Choices(fps_options(23.1))
    assert picker.currentData() is None
    seen = []
    picker.changed.connect(lambda: seen.append(picker.currentData()))
    picker.findChildren(type(picker._group.button(0)))[3].click()
    assert picker.currentData() == 60 and seen == [60]


def test_both_picker_rows_fit_the_minimum_width(qapp):
    """Every option is on screen at once now, so the rows must fit."""
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

    from arctis_sound_manager.gui.clip_editor import (SIZE_CHOICES, _Choices,
                                                      fps_options)

    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setSpacing(8)
    layout.addWidget(QLabel("Share size:"))
    layout.addWidget(_Choices(SIZE_CHOICES))
    layout.addSpacing(16)
    layout.addWidget(QLabel("Frame rate:"))
    layout.addWidget(_Choices(fps_options(23.1)))
    assert row.sizeHint().width() <= MIN_SIZE.width() - 2 * 18
