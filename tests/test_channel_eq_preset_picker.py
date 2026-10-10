# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""EQ preset picker on the Channels page cards (#256).

Each card but Master lists the channel's favorites from the Equalizer page
and applies the one picked, like the preset dropdown in GG's own mixer.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import home_page
from arctis_sound_manager.gui.home_page import AudioCard, HomePage


@pytest.fixture
def card():
    QApplication.instance() or QApplication([])
    return AudioCard("Game", "#ff0000")


def _items(card) -> list[str]:
    return list(card._preset_sig[0])


class _FakeDialog:
    """Stands in for the Equalizer page's preset search window."""
    DialogCode = SimpleNamespace(Accepted=1)
    answer: str | None = None
    opened: list[dict] = []

    def __init__(self, presets, parent=None, current=None, allow_delete=True):
        _FakeDialog.opened.append(
            {"names": list(presets), "current": current, "allow_delete": allow_delete})
        self.selected_name = _FakeDialog.answer

    def exec(self):
        return 1

    def result(self):
        return 1 if self.selected_name else 0


@pytest.fixture
def fake_dialog(monkeypatch):
    from arctis_sound_manager.gui import sonar_page
    _FakeDialog.opened = []
    monkeypatch.setattr(sonar_page, "_PresetSearchDialog", _FakeDialog)
    monkeypatch.setattr(sonar_page, "_list_presets", lambda ch: {})
    return _FakeDialog


def test_picker_hidden_until_presets_given(card):
    assert card._preset_btn.isHidden()


def test_picker_lists_favorites_with_active_selected(card, fake_dialog):
    card.set_presets(["Flat", "FPS", "Racing"], "FPS")
    assert not card._preset_btn.isHidden()
    assert _items(card) == ["Flat", "FPS", "Racing"]
    card._on_preset_clicked()
    assert fake_dialog.opened == [
        {"names": ["Flat", "FPS", "Racing"], "current": "FPS", "allow_delete": False}]


def test_active_preset_listed_even_when_not_a_favorite(card):
    """The picker must show what is really applied, not a favorite that isn't."""
    card.set_presets(["FPS", "Racing"], "Podcast")
    assert _items(card) == ["Podcast", "FPS", "Racing"]


def test_no_favorites_disables_the_button(card):
    card.set_presets([], "")
    assert not card._preset_btn.isHidden()
    assert not card._preset_btn.isEnabled()


def test_none_hides_the_picker(card):
    card.set_presets(["FPS"], "FPS")
    card.set_presets(None)
    assert card._preset_btn.isHidden()


def test_picking_calls_back_once_and_not_for_the_active_one(card, fake_dialog):
    picked = []
    card.set_on_preset(picked.append, "game")
    card.set_presets(["Flat", "FPS"], "Flat")
    fake_dialog.answer = "Flat"           # already active: nothing to apply
    card._on_preset_clicked()
    fake_dialog.answer = None             # cancelled
    card._on_preset_clicked()
    fake_dialog.answer = "FPS"
    card._on_preset_clicked()
    assert picked == ["FPS"]


def test_busy_locks_the_button(card):
    card.set_presets(["Flat", "FPS"], "Flat")
    card.set_preset_busy(True)
    assert not card._preset_btn.isEnabled()
    card.set_preset_busy(False)
    assert card._preset_btn.isEnabled()


class _FakePicker:
    def __init__(self):
        self.picked: list[str] = []

    def options(self):
        return [("", "Arctis Nova Pro"), ("bt", "Earbuds")]

    def current_id(self):
        return ""

    def pick(self, device_id):
        self.picked.append(device_id)
        self.label = dict(self.options())[device_id]

    label = "Arctis Nova Pro"

    def current_label(self):
        return self.label


def test_output_button_hidden_until_picker_given(card):
    assert card._output_btn.isHidden()
    card.set_output_picker(_FakePicker())
    assert not card._output_btn.isHidden()


def test_output_button_picks_through_the_selector(card, monkeypatch):
    opened = []

    class _Dlg:
        DialogCode = SimpleNamespace(Accepted=1)
        answer = None

        def __init__(self, title, entries, parent=None, current=None):
            opened.append((entries, current))
            self.selected_key = _Dlg.answer

        def exec(self):
            return 1

        def result(self):
            return 1 if self.selected_key is not None else 0

    monkeypatch.setattr(home_page, "SearchPickDialog", _Dlg)
    picker = _FakePicker()
    card.set_output_picker(picker)
    _Dlg.answer = ""                      # the current one: nothing to do
    card._on_output_clicked()
    _Dlg.answer = None                    # cancelled
    card._on_output_clicked()
    _Dlg.answer = "bt"
    card._on_output_clicked()
    assert picker.picked == ["bt"]
    assert opened[0] == ([("", "Arctis Nova Pro"), ("bt", "Earbuds")], "")


def test_card_shows_the_selected_output_without_opening_the_picker(card, monkeypatch):
    """#316: the device a channel plays on is readable on the card itself."""
    assert card._output_lbl.isHidden()
    picker = _FakePicker()
    card.set_output_picker(picker)
    assert not card._output_lbl.isHidden()
    assert card._output_lbl.toolTip() == "Arctis Nova Pro"

    class _Dlg:
        DialogCode = SimpleNamespace(Accepted=1)

        def __init__(self, *a, **k):
            self.selected_key = "bt"

        def exec(self):
            return 1

        def result(self):
            return 1

    monkeypatch.setattr(home_page, "SearchPickDialog", _Dlg)
    card._on_output_clicked()
    assert card._output_lbl.toolTip() == "Earbuds"


def test_no_output_label_without_a_picker(card):
    card.set_output_picker(None)
    assert card._output_lbl.isHidden()


# ── HomePage wiring ────────────────────────────────────────────────────────────

class _FakeCard:
    def __init__(self):
        self.presets = "unset"
        self.busy: list[bool] = []

    def set_presets(self, names, active=""):
        self.presets = (names, active) if names is not None else None

    def set_preset_busy(self, busy):
        self.busy.append(busy)


class _FakeApplier:
    def __init__(self, running=False):
        self.running = running
        self.applied: list[tuple[str, str]] = []

    def is_running(self):
        return self.running

    def apply(self, channel, name):
        self.applied.append((channel, name))


def _page(mode: str, monkeypatch, running=False):
    monkeypatch.setattr(home_page, "current_eq_mode", lambda: mode)
    monkeypatch.setattr(home_page, "list_sonar_channel_presets",
                        lambda ch, limit=None: [f"{ch}-fav"])
    monkeypatch.setattr(home_page, "get_sonar_active_preset", lambda ch: "")
    channels = ("game", "chat", "media", "aux", "output")
    return SimpleNamespace(
        _preset_cards={ch: _FakeCard() for ch in channels},
        _preset_appliers={ch: _FakeApplier(running) for ch in channels},
    )


def test_master_has_no_picker(monkeypatch):
    page = _page("sonar", monkeypatch)
    assert "master" not in page._preset_cards
    HomePage._refresh_eq_presets(page)
    assert page._preset_cards["game"].presets == (["game-fav"], "Flat")


def test_pickers_hidden_outside_sonar_mode(monkeypatch):
    """Custom EQ is one global curve, not a per-channel preset."""
    page = _page("custom", monkeypatch)
    HomePage._refresh_eq_presets(page)
    assert all(c.presets is None for c in page._preset_cards.values())


def test_refresh_leaves_a_card_alone_while_its_preset_applies(monkeypatch):
    page = _page("sonar", monkeypatch, running=True)
    HomePage._refresh_eq_presets(page)
    assert page._preset_cards["game"].presets == "unset"


def test_choosing_applies_on_that_channel_and_locks_the_picker(monkeypatch):
    page = _page("sonar", monkeypatch)
    HomePage._on_eq_preset_chosen(page, "media", "Movie")
    assert page._preset_appliers["media"].applied == [("media", "Movie")]
    assert page._preset_cards["media"].busy == [True]
