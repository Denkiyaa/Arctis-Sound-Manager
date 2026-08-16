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


def test_every_offered_frame_rate_has_a_label():
    """The rate menu builds its keys as `clip_fps_<n>`, so adding a rate to
    FPS_CHOICES without adding a line to en.ini shows the user the key itself —
    "ui.clip_fps_15" in the menu, which is what happened when 15 was added."""
    import configparser
    from pathlib import Path

    from arctis_sound_manager.clip_export import FPS_CHOICES
    from arctis_sound_manager.lang_sanitize import sanitize_ini_text

    english = (Path(__file__).parent.parent / "src" / "arctis_sound_manager"
               / "lang" / "en.ini")
    parser = configparser.ConfigParser()
    parser.read_string(sanitize_ini_text(english.read_text(encoding="utf-8")))
    keys = {k for section in parser.sections() for k in parser[section]}

    for rate in FPS_CHOICES:
        assert f"clip_fps_{rate}" in keys, f"no label for {rate} fps"
    assert "clip_fps_source" in keys


# ── what an editor leaves behind ──────────────────────────────────────────────

def test_release_hands_the_players_back_not_just_the_python_names():
    """The players are parented to the dialog, so clearing the lists frees
    nothing — they and their decoder threads outlive the close. The crash dump
    behind this had 116 threads and a gigabyte of memory after an evening of
    opening clips."""
    from arctis_sound_manager.gui.clip_editor import _ChannelMixer

    class _Fake:
        def __init__(self):
            self.stopped = self.deleted = False

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True

    mixer = _ChannelMixer(None)
    players = [_Fake(), _Fake()]
    outputs = [_Fake(), _Fake()]
    mixer._players.extend(players)
    mixer._outputs.extend(outputs)

    mixer.release()

    assert all(p.stopped and p.deleted for p in players)
    assert all(o.deleted for o in outputs)
    assert mixer._players == [] and mixer._outputs == []


# ── the playhead, and why it is polled ────────────────────────────────────────

def test_the_playhead_is_polled_not_pushed():
    """Qt's ffmpeg backend drives the clock from its audio renderer thread, so
    a Python slot on positionChanged is entered *from that thread* — PySide
    takes the GIL there while the thread holds a Qt mutex, and the GUI thread
    calling into the same plugin holds the GIL and wants that mutex. A live
    hang showed both halves: 38 threads in futex_wait behind a window that
    would not close. Nothing may connect to it again."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "src" / "arctis_sound_manager"
              / "gui" / "clip_editor.py").read_text(encoding="utf-8")

    assert "positionChanged.connect" not in source
    assert "durationChanged.connect" not in source


def test_polling_moves_the_band_and_notices_the_duration():
    import types

    from arctis_sound_manager.gui.clip_editor import ClipEditor

    moved: list[float] = []
    durations: list[int] = []

    editor = types.SimpleNamespace(
        _player=types.SimpleNamespace(position=lambda: 4_000,
                                      duration=lambda: 30_000),
        _band=types.SimpleNamespace(set_position=moved.append, end_s=99.0),
        _last_duration_ms=-1,
        _is_playing=lambda: False,
        _on_media_duration=durations.append,
    )
    editor._on_position = lambda ms: ClipEditor._on_position(editor, ms)

    ClipEditor._poll_position(editor)
    ClipEditor._poll_position(editor)          # duration reported once only

    assert moved == [4.0, 4.0]
    assert durations == [30_000]
