# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Following the game: arming the buffer when one starts, letting it go when
one ends.

A rolling buffer is only worth having if it is already running when something
happens, and what people forget is arming it — "I closed the game and the
capture kept going" and "it never started" are the same gap seen from either
end. Stopping matters for its own reason: a capture with no game behind it
holds a screen's worth of frames in memory and keeps an encoder busy for a
recording nobody will ask for.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import clips_page


@pytest.fixture
def page(tmp_path, monkeypatch):
    """A Clips page whose capture is a stand-in, so nothing touches the screen."""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(clips_page, "CLIP_DIR", tmp_path)
    monkeypatch.setattr(clips_page.ClipsPage, "_queue_thumbnail",
                        lambda self, clip: None)
    # The page polls once through the event loop at startup; nothing has been
    # stubbed at that point, so keep it out of the way until the test asks.
    monkeypatch.setattr(clips_page.ClipsPage, "_poll_game", lambda self: None)

    widget = clips_page.ClipsPage()
    monkeypatch.undo()
    monkeypatch.setattr(clips_page, "CLIP_DIR", tmp_path)

    # A screen has been picked at some point, which is the ordinary state and
    # the only one where an automatic start is allowed to happen at all. The
    # tests about *not* having one say so themselves.
    monkeypatch.setattr("arctis_sound_manager.clip_capture.has_saved_source",
                        lambda: True)

    started: list[bool] = []
    stopped: list[bool] = []

    # Stand in for the start itself rather than for the toggle: a start can
    # fail — a dismissed portal picker is the ordinary way — and the page has
    # to tell the two apart. `start_succeeds` is how a test says which.
    widget.start_succeeds = True

    def _fake_start(self) -> None:
        started.append(True)
        if self.start_succeeds:
            self._capture = object()

    monkeypatch.setattr(type(widget), "_start_capture", _fake_start)
    monkeypatch.setattr(type(widget), "_stop_capture",
                        lambda self: (stopped.append(True),
                                      setattr(self, "_capture", None))[0])
    widget.started, widget.stopped = started, stopped
    yield widget
    widget.deleteLater()


def _game(monkeypatch, name):
    monkeypatch.setattr("arctis_sound_manager.clip_capture.detect_game",
                        lambda: name)


def test_a_game_starting_arms_the_buffer(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)

    page._poll_game()

    assert page.started == [True]


def test_the_buffer_is_not_armed_twice(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)

    page._poll_game()
    page._poll_game()

    assert page.started == [True]


def test_the_game_going_quiet_does_not_stop_it_immediately(page, monkeypatch):
    """A loading screen or a cutscene is silence, and tearing the pipeline down
    there throws the buffer away and costs a portal prompt to rebuild."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()

    assert page.stopped == []


def test_a_game_that_stays_gone_lets_the_capture_go(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()
    # Far enough past the grace that the next poll is decisive.
    page._game_gone_since -= clips_page._GAME_GONE_GRACE_S + 1
    page._poll_game()

    assert page.stopped == [True]


def test_a_capture_the_user_started_is_never_stopped_for_them(page, monkeypatch):
    """Auto-stop only takes back what auto-start gave. Someone who pressed
    Start is recording deliberately and gets to decide when it ends."""
    page._autostart.setChecked(True)
    page._capture = object()          # as if Start had been pressed
    page._auto_started = False

    _game(monkeypatch, None)
    for _ in range(3):
        page._poll_game()

    assert page.stopped == []
    # The countdown never even starts: there is nothing here to take back.
    assert page._game_gone_since is None


def test_switching_it_off_stops_following_the_game(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(False)

    page._poll_game()

    assert page.started == []


def test_a_game_returning_during_the_grace_keeps_the_buffer(page, monkeypatch):
    """Coming back from a loading screen must reset the countdown, not carry
    half of it into the next silence."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()
    _game(monkeypatch, "GenshinImpact")
    page._poll_game()

    assert page._game_gone_since is None
    assert page.stopped == []


def test_it_is_on_unless_the_user_says_otherwise():
    """The moment worth clipping has already happened by the time anyone thinks
    to press Start — which is the whole reason the buffer exists."""
    from arctis_sound_manager.settings import GeneralSettings

    assert GeneralSettings().clips_autostart is True


# ── a start that did not take ──────────────────────────────────────────────────

def test_a_start_that_did_not_take_is_not_retried_every_tick(page, monkeypatch):
    """The ordinary way an automatic start fails is the portal picker being
    dismissed — and the poll is on a timer, so retrying turns one "no" into a
    dialog every few seconds for as long as the game runs. Ask once."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page.start_succeeds = False

    page._poll_game()
    page._poll_game()
    page._poll_game()

    assert page.started == [True]
    assert page._capture is None


def test_the_game_going_away_restores_the_attempt(page, monkeypatch):
    """Whatever went wrong belonged to that session, not to the next game."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page.start_succeeds = False
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()

    _game(monkeypatch, "Deadlock")
    page.start_succeeds = True
    page._poll_game()

    assert page.started == [True, True]
    assert page._capture is not None


def test_pressing_start_answers_the_question(page, monkeypatch):
    """Start is the way back in after declining — it must not stay blocked."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page.start_succeeds = False
    page._poll_game()
    assert page._auto_start_blocked

    page.start_succeeds = True
    page._on_toggle()

    assert page._auto_start_blocked is False
    assert page._capture is not None


# ── the picker is never opened uninvited ───────────────────────────────────────

def _saved_source(monkeypatch, exists: bool):
    monkeypatch.setattr("arctis_sound_manager.clip_capture.has_saved_source",
                        lambda: exists)


def test_no_saved_screen_means_no_picker(page, monkeypatch):
    """The portal must ask when nothing is saved, so the automatic start does
    not go there at all — the dialog would land on top of the game, for a
    question the user never opened this page to answer."""
    _game(monkeypatch, "GenshinImpact")
    _saved_source(monkeypatch, False)
    page._autostart.setChecked(True)

    page._poll_game()
    page._poll_game()

    assert page.started == []
    assert "Press Start" in page._status.text()


def test_a_saved_screen_starts_in_silence(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    _saved_source(monkeypatch, True)
    page._autostart.setChecked(True)

    page._poll_game()

    assert page.started == [True]
