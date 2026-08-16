# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A left-click on the tray item must not build the window on the spot.

The slot runs inside KStatusNotifierItem::activate(), a D-Bus call the tray
host is still on the stack for. Building the main window there takes long
enough that Qt processes events underneath it, and a status poll landing in
that window hides the battery item — which deletes the very
KStatusNotifierItem whose activate() is on the stack. Returning into freed
memory took the whole app down with SIGSEGV, tray icon included, which from
the outside looks like the app closing itself.

Returning to the event loop first is the whole fix, so that is what is pinned
here: the click schedules the window, it does not open it inline.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from arctis_sound_manager.gui.systray_app import QSystrayApp


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_left_click_schedules_the_window_instead_of_opening_it(qapp):
    tray = MagicMock()

    QSystrayApp._on_tray_activated(tray, QSystemTrayIcon.ActivationReason.Trigger)
    tray.open_main_window.assert_not_called()

    qapp.processEvents()
    tray.open_main_window.assert_called_once_with()


def test_other_activation_reasons_are_left_to_the_context_menu(qapp):
    tray = MagicMock()

    QSystrayApp._on_tray_activated(tray, QSystemTrayIcon.ActivationReason.Context)
    qapp.processEvents()

    tray.open_main_window.assert_not_called()


# ── clicking again while it is still coming up ────────────────────────────────

def test_a_second_click_during_the_build_does_not_build_a_second_window(monkeypatch):
    """Building the main window takes seconds and Qt keeps delivering events
    through it, so the click that lands mid-build used to find `_main_app`
    unset and start another one. Ten impatient clicks, ten windows."""
    import types

    from arctis_sound_manager.gui import systray_app

    built: list[object] = []

    class _FakeMainApp:
        def __init__(self, _app, _level):
            built.append(self)
            self.main_window = types.SimpleNamespace(
                show=lambda: None, raise_=lambda: None,
                activateWindow=lambda: None)
            # The impatient click, arriving while this constructor runs.
            if len(built) == 1:
                systray_app.QSystrayApp.open_main_window(stub)

    monkeypatch.setattr(systray_app, "QMainApp", _FakeMainApp)

    stub = types.SimpleNamespace(app=object(),
                                 logger=types.SimpleNamespace(level=0))

    systray_app.QSystrayApp.open_main_window(stub)

    assert len(built) == 1


def test_a_later_click_reuses_the_window_it_already_built(monkeypatch):
    import types

    from arctis_sound_manager.gui import systray_app

    built: list[object] = []
    shown: list[int] = []

    class _FakeMainApp:
        def __init__(self, _app, _level):
            built.append(self)
            self.main_window = types.SimpleNamespace(
                show=lambda: shown.append(1), raise_=lambda: None,
                activateWindow=lambda: None)

    monkeypatch.setattr(systray_app, "QMainApp", _FakeMainApp)
    stub = types.SimpleNamespace(app=object(),
                                 logger=types.SimpleNamespace(level=0))

    systray_app.QSystrayApp.open_main_window(stub)
    systray_app.QSystrayApp.open_main_window(stub)

    assert len(built) == 1 and len(shown) == 2
