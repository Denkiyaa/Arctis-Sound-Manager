# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Mic card on the Channels page (#315).

Its slider is the volume of what applications record (the Sonar Micro EQ's
output, or the default input when the mic chain is down), its button picks
the input feeding it — the same setting as the Settings page — and it lists
the applications recording.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import home_page
from arctis_sound_manager.pactl import micro_source_options


def _source(name, index, vol=0.5, **props):
    return SimpleNamespace(
        name=name, index=index, description=props.pop("desc", ""),
        volume=SimpleNamespace(value_flat=vol),
        proplist={"node.name": name, **props})


class _Pulse:
    def __init__(self, sources, outputs=(), default="alsa_input.mic"):
        self._sources = list(sources)
        self._outputs = list(outputs)
        self._default = default
        self.set_calls = []

    def source_list(self):
        return self._sources

    def source_output_list(self):
        return self._outputs

    def server_info(self):
        return SimpleNamespace(default_source_name=self._default)

    def volume_set_all_chans(self, obj, value):
        self.set_calls.append((obj.name, value))


@pytest.fixture
def page():
    QApplication.instance() or QApplication([])
    widget = home_page.HomePage()
    yield widget
    widget.deleteLater()


def test_input_list_skips_monitors_and_asm_nodes():
    sources = [
        _source("alsa_input.usb-Arctis.mono", 1, desc="Arctis Nova Pro"),
        _source("alsa_output.hdmi.monitor", 2),
        _source("effect_output.sonar-micro-eq", 3),
        _source("bluez_input.30:96", 4, desc="FreeBuds"),
        _source("alsa_input.usb-Arctis.mono", 5, desc="Arctis Nova Pro"),
    ]
    assert micro_source_options(sources) == [
        ("alsa_input.usb-Arctis.mono", "Arctis Nova Pro"),
        ("bluez_input.30:96", "FreeBuds"),
    ]


def test_slider_follows_and_drives_the_micro_eq_output(page):
    eq = _source("effect_output.sonar-micro-eq", 7, vol=0.8)
    pulse = _Pulse([_source("alsa_input.mic", 1), eq])
    page._poll_mic(pulse)
    assert page._mic_card._slider.value() == 80

    page._get_pulse = lambda: pulse
    page._on_mic_volume_changed(40)
    assert pulse.set_calls == [("effect_output.sonar-micro-eq", 0.4)]


def test_without_the_mic_chain_it_falls_back_to_the_default_input(page):
    pulse = _Pulse([_source("alsa_input.mic", 1, vol=0.3)], default="alsa_input.mic")
    page._poll_mic(pulse)
    assert page._source_mic.name == "alsa_input.mic"
    assert page._mic_card._slider.value() == 30


def test_lists_the_applications_recording_but_not_peak_meters(page):
    eq = _source("effect_output.sonar-micro-eq", 7)
    outputs = [
        SimpleNamespace(index=10, source=7, proplist={"application.name": "Discord"}),
        SimpleNamespace(index=11, source=7, proplist={"application.name": "pavucontrol",
                                                      "stream.monitor": "true"}),
        SimpleNamespace(index=12, source=1, proplist={"application.name": "Other mic"}),
    ]
    page._poll_mic(_Pulse([_source("alsa_input.mic", 1), eq], outputs))
    tags = [page._mic_card._apps_area.itemAt(i).widget().toolTip()
            for i in range(page._mic_card._apps_area.count())]
    assert tags == ["Discord"]


def test_picking_an_input_goes_through_the_daemon_setting(page, monkeypatch):
    sent = []
    from arctis_sound_manager.gui import dbus_wrapper
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting",
                        staticmethod(lambda name, value: sent.append((name, value))))
    page._mic_picker.pick("bluez_input.30:96")
    assert sent == [("micro_input_source", "bluez_input.30:96")]


def test_the_input_picker_offers_auto_and_manual_first(page):
    page._mic_picker._get_pulse = lambda: _Pulse([_source("alsa_input.mic", 1, desc="USB mic")])
    ids = [i for i, _ in page._mic_picker.options()]
    assert ids == ["__auto__", "__manual__", "alsa_input.mic"]


def test_mic_card_is_leftmost(page):
    assert page._cards_layout.itemAt(0).widget() is page._mic_card


def test_any_card_can_be_hidden_and_the_choice_persists(page, monkeypatch):
    from arctis_sound_manager.settings import GeneralSettings
    store = GeneralSettings()
    monkeypatch.setattr(GeneralSettings, "read_from_file", staticmethod(lambda: store))
    monkeypatch.setattr(GeneralSettings, "write_to_file", lambda self: None)

    for channel in ("mic", "master", "game", "chat", "media"):
        page._set_channel_visible(channel, False)
        assert page._channel_card(channel).isHidden()
    assert set(store.hidden_channels) == {"mic", "master", "game", "chat", "media"}

    page._set_channel_visible("game", True)
    assert not page._game_card.isHidden()
    assert "game" not in store.hidden_channels


def test_every_theme_has_a_mic_colour_and_old_ones_derive_it():
    from arctis_sound_manager.gui import theme
    for tid, colors in theme.THEMES.items():
        assert theme._validate_color(colors["COLOR_MIC"]), tid
    assert theme.default_for("COLOR_MIC", {"COLOR_MASTER": "#123456"}) == "#123456"


def test_a_profile_remembers_which_cards_are_shown(monkeypatch):
    from arctis_sound_manager import profile_manager
    from arctis_sound_manager.settings import GeneralSettings
    store = GeneralSettings()
    store.hidden_channels = ["master"]
    store.output_channel_visible = False
    monkeypatch.setattr(GeneralSettings, "read_from_file", staticmethod(lambda: store))
    monkeypatch.setattr(GeneralSettings, "write_to_file", lambda self: None)

    snap = profile_manager._snapshot_channels()
    assert snap == {"hidden": ["master"], "aux": False, "output": False}

    store.hidden_channels = []
    store.output_channel_visible = True
    profile_manager._apply_channels(snap)
    assert store.hidden_channels == ["master"] and store.output_channel_visible is False
