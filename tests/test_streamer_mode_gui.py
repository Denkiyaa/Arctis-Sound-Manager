# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Streamer mode on the Channels page: a Stream slider and mute on the five
cards that feed the stream mix, shown only while the mode is on.

stream_mix is replaced by a stand-in module: the real one talks to pw-cli, and
what is under test here is the GUI wiring (what is persisted, what is applied,
what the daemon is told), not the audio graph.
"""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QCheckBox

CHANNELS = ("game", "chat", "media", "aux", "mic")


@pytest.fixture
def fake_mix(monkeypatch):
    mod = types.ModuleType("arctis_sound_manager.stream_mix")
    mod.STREAM_CHANNELS = CHANNELS
    mod.state = {c: {"volume": 100, "muted": False} for c in CHANNELS}
    mod.state["chat"] = {"volume": 35, "muted": True}
    mod.saved, mod.applied = [], []
    mod.load_stream_mix = lambda: {c: dict(v) for c, v in mod.state.items()}
    mod.save_stream_mix_channel = (
        lambda channel, volume=None, muted=None: mod.saved.append((channel, volume, muted)))
    mod.apply_stream_mix = lambda state=None: mod.applied.append(state) or True
    monkeypatch.setitem(sys.modules, "arctis_sound_manager.stream_mix", mod)
    monkeypatch.setattr("arctis_sound_manager.stream_mix", mod, raising=False)
    return mod


@pytest.fixture
def page(fake_mix, monkeypatch):
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui import home_page
    from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
    calls = []
    monkeypatch.setattr(DbusWrapper, "change_setting",
                        staticmethod(lambda k, v: calls.append((k, v))))
    widget = home_page.HomePage()
    widget.dbus_calls = calls
    yield widget
    widget.deleteLater()


def _cards(page):
    return {"game": page._game_card, "chat": page._chat_card,
            "media": page._media_card, "aux": page._aux_card, "mic": page._mic_card}


def test_the_controls_are_hidden_while_the_mode_is_off(page):
    page._apply_streamer_visibility(False)

    for card in (*_cards(page).values(), page._master_card, page._ext_card):
        assert card._stream_widget.isHidden()


def test_turning_it_on_shows_them_on_the_five_stream_cards_only(page):
    page._apply_streamer_visibility(True)

    for card in _cards(page).values():
        assert not card._stream_widget.isHidden()
    assert page._master_card._stream_widget.isHidden()
    assert page._ext_card._stream_widget.isHidden()


def test_the_controls_start_from_the_saved_stream_mix(page):
    page._apply_streamer_visibility(True)

    chat = page._chat_card
    assert chat._stream_slider.value() == 35
    assert chat._stream_mute_btn.isChecked()
    assert page._game_card._stream_slider.value() == 100


def test_loading_the_state_does_not_write_it_back(page, fake_mix):
    page._apply_streamer_visibility(True)

    assert fake_mix.saved == []


def test_moving_the_slider_persists_and_schedules_one_apply(page, fake_mix):
    page._apply_streamer_visibility(True)
    page._game_card._stream_slider.setValue(40)
    page._game_card._stream_slider.setValue(42)

    assert page._game_card._stream_lbl.text().endswith("42%")
    assert fake_mix.saved[-1] == ("game", 42, False)
    assert page._stream_apply_timer.isActive()
    assert page._stream_apply_timer.isSingleShot()
    assert fake_mix.applied == []  # debounced, not yet


def test_the_debounce_runs_apply_off_the_gui_thread(page, fake_mix):
    import threading
    seen = []
    fake_mix.apply_stream_mix = lambda state=None: seen.append(threading.current_thread())
    page._run_stream_apply()
    for _ in range(100):
        if seen:
            break
        threading.Event().wait(0.02)

    assert seen and seen[0] is not threading.main_thread()


def test_mute_toggles_and_persists(page, fake_mix):
    page._apply_streamer_visibility(True)
    page._media_card._stream_mute_btn.click()

    assert fake_mix.saved[-1] == ("media", 100, True)
    page._media_card._stream_mute_btn.click()
    assert fake_mix.saved[-1] == ("media", 100, False)


def test_toggle_tells_the_daemon(page):
    page._set_streamer_mode(True)
    page._set_streamer_mode(False)

    assert page.dbus_calls == [("streamer_mode", True), ("streamer_mode", False)]


def test_the_menu_has_a_streamer_row_with_a_tooltip_and_signal(page):
    from arctis_sound_manager.gui.profile_bar import ProfileBar

    bar = ProfileBar()
    menu = bar._build_channels_menu()
    boxes = {a.defaultWidget().property("channel"): a.defaultWidget()
             for a in menu.actions() if not a.isSeparator()}
    got = []
    bar.sig_toggle_streamer.connect(got.append)
    boxes["streamer"].setChecked(True)

    assert isinstance(boxes["streamer"], QCheckBox)
    assert "OBS" in boxes["streamer"].toolTip()
    assert got == [True]


def test_the_en_ini_keys_exist():
    from pathlib import Path
    text = (Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "lang" / "en.ini").read_text()
    for key in ("streamer_mode", "streamer_mode_tooltip", "stream_label", "stream_mute"):
        assert f"\n{key} = " in text
