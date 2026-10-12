# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scrolling a page past a combo must not pick something in it.

A stock QComboBox under the pointer takes the wheel and reports the step as
`activated` — the signal the output pickers treat as a deliberate choice — so
scrolling the Equalizer page could send a channel to another device.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _scroll(widget) -> None:
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def _combo(cls):
    combo = cls()
    combo.addItems(["Headset", "FreeBuds", "HDMI"])
    picked = []
    combo.activated.connect(picked.append)
    return combo, picked


def test_a_stock_combo_takes_the_wheel_as_a_pick(qapp):
    """The behaviour being guarded against, so this test fails loudly if Qt
    ever stops doing it and the subclass can go."""
    combo, picked = _combo(QComboBox)
    _scroll(combo)
    assert picked == [1]


def test_scrolling_over_the_picker_changes_nothing(qapp):
    from arctis_sound_manager.gui.qt_widgets.no_wheel_combo import NoWheelComboBox

    combo, picked = _combo(NoWheelComboBox)
    _scroll(combo)
    assert combo.currentIndex() == 0 and picked == []


def test_the_output_pickers_use_it(qapp):
    from arctis_sound_manager.gui.channel_output_selector import ChannelOutputSelector
    from arctis_sound_manager.gui.output_selector import OutputSelector
    from arctis_sound_manager.gui.qt_widgets.no_wheel_combo import NoWheelComboBox

    for picker in (ChannelOutputSelector("game"), OutputSelector()):
        assert isinstance(picker._combo, NoWheelComboBox)
