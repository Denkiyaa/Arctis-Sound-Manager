# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dragging an application onto a channel card.

The cards always carried a drop callback and a highlight style, but never
accepted a drop: moving an app was the click menu only, and dragging did
nothing. These tests pin the drop side and the guard that keeps the 500 ms
poll from deleting a tag while it is being dragged.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import home_page
from arctis_sound_manager.gui.home_page import AudioCard, _StreamDragSource


@pytest.fixture(autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _mime(si=42, app="StardewModdingAPI", pid=7) -> QMimeData:
    mime = QMimeData()
    mime.setData(home_page._STREAM_MIME,
                 json.dumps({"si": si, "app": app, "pid": pid}).encode())
    return mime


def _enter(card, mime) -> bool:
    ev = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.MoveAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    card.dragEnterEvent(ev)
    return ev.isAccepted()


def test_a_card_takes_a_dragged_app_and_moves_it(monkeypatch):
    # The move runs after the drag loop returns; run it inline here.
    monkeypatch.setattr(home_page.QTimer, "singleShot", lambda _ms, fn: fn())
    card = AudioCard("Chat", "#00ff00")
    moved = []
    card.set_on_drop(lambda si, app, pid: moved.append((si, app, pid)))

    mime = _mime()
    assert _enter(card, mime)
    card.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.MoveAction, mime,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert moved == [(42, "StardewModdingAPI", 7)]


def test_master_refuses_a_drop():
    """Master is a volume, not a place a stream can play."""
    card = AudioCard("Master", "#ffffff")
    assert not _enter(card, _mime())


def test_foreign_drags_are_refused():
    card = AudioCard("Game", "#ff0000")
    card.set_on_drop(lambda *a: None)
    mime = QMimeData()
    mime.setText("some text from another app")
    assert not _enter(card, mime)
    assert home_page._stream_from_mime(mime) is None


def test_a_short_move_is_still_a_click():
    """Below the drag distance nothing starts, so the click opens the menu."""
    tag = home_page._AppTag("Firefox", 1, 2, "#ff0000")
    tag._press_pos = QPoint(0, 0)

    class _Ev:
        def buttons(self):
            return Qt.MouseButton.LeftButton

        def position(self):
            return QPointF(1, 0)

    assert tag._drag_move(_Ev()) is False
    assert tag._press_pos == QPoint(0, 0)


def test_tags_are_not_rebuilt_while_one_is_dragged(monkeypatch):
    card = AudioCard("Game", "#ff0000")
    card._app_sig = ()
    page = home_page.HomePage.__new__(home_page.HomePage)
    monkeypatch.setattr(_StreamDragSource, "dragging", True)
    page._apply_app_rows(card, [("Firefox", 1, 2, ("", "", ""))])
    assert card._app_sig == ()
    monkeypatch.setattr(_StreamDragSource, "dragging", False)
    page._apply_app_rows(card, [("Firefox", 1, 2, ("", "", ""))])
    assert card._app_sig != ()
