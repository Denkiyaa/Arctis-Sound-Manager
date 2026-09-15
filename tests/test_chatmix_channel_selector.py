# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ChatMix channel selector GUI (#249).

A user's headset always crossfades Game against Chat on the physical dial;
Media and Aux never move. This lets a per-install choice add Media and/or
Aux to the dial's non-chat side, via a small checkbox on each of those two
cards ("Include in ChatMix"). Game and Chat never show it — they are the
dial's fixed, always-on halves.

Uses the lightweight SimpleNamespace fake-self pattern from
tests/test_home_page_app_name.py rather than instantiating a real
QApplication/AudioCard: the methods under test only touch attributes they
are handed, so a fake standing in for those attributes is enough.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.home_page import AudioCard, HomePage


# ── AudioCard: the checkbox itself ──────────────────────────────────────────

class _FakeCheckbox:
    """Stands in for the real QCheckBox: records blockSignals/setChecked calls
    in order, so the blockSignals-during-population guard can be verified
    without a real Qt event loop."""

    def __init__(self):
        self._checked = False
        self.calls: list[tuple[str, object]] = []

    def blockSignals(self, value):
        self.calls.append(("blockSignals", value))

    def setChecked(self, value):
        self.calls.append(("setChecked", value))
        self._checked = value

    def isChecked(self):
        return self._checked


def test_set_chatmix_checked_blocks_signals_around_the_write():
    """Same guard as set_device_options(): without it, setting the initial
    state during population would fire the toggle callback and re-write the
    setting right back to what it already was."""
    fake_self = SimpleNamespace(_chatmix_checkbox=_FakeCheckbox())

    AudioCard.set_chatmix_checked(fake_self, True)

    assert fake_self._chatmix_checkbox.calls == [
        ("blockSignals", True),
        ("setChecked", True),
        ("blockSignals", False),
    ]
    assert fake_self._chatmix_checkbox.isChecked() is True


def test_set_chatmix_checked_reflects_false_too():
    fake_self = SimpleNamespace(_chatmix_checkbox=_FakeCheckbox())
    fake_self._chatmix_checkbox.setChecked(True)  # pre-existing state

    AudioCard.set_chatmix_checked(fake_self, False)

    assert fake_self._chatmix_checkbox.isChecked() is False


def test_chatmix_toggled_invokes_the_registered_callback():
    calls = []
    fake_self = SimpleNamespace(_chatmix_toggle_cb=lambda enabled: calls.append(enabled))

    AudioCard._on_chatmix_toggled(fake_self, True)

    assert calls == [True]


def test_chatmix_toggled_is_a_noop_without_a_registered_callback():
    fake_self = SimpleNamespace(_chatmix_toggle_cb=None)

    AudioCard._on_chatmix_toggled(fake_self, True)  # must not raise


# ── HomePage: reflecting the setting on population ─────────────────────────

def _fake_card():
    card = MagicMock()
    card.isHidden.return_value = False
    return card


def test_refresh_chatmix_toggles_reflects_current_setting(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_extra_channels = ["media"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    media_card = _fake_card()
    aux_card = _fake_card()
    fake_self = SimpleNamespace(_media_card=media_card, _aux_card=aux_card)

    HomePage._refresh_chatmix_toggles(fake_self)

    media_card.set_chatmix_toggle_visible.assert_called_once_with(True)
    media_card.set_chatmix_checked.assert_called_once_with(True)
    # Aux card is not hidden in this fake, so its toggle is relevant too.
    aux_card.set_chatmix_toggle_visible.assert_called_once_with(True)
    aux_card.set_chatmix_checked.assert_called_once_with(False)


def test_refresh_chatmix_toggles_hides_aux_toggle_when_aux_card_is_hidden(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_extra_channels = ["aux"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    media_card = _fake_card()
    aux_card = _fake_card()
    aux_card.isHidden.return_value = True  # Aux channel itself is off
    fake_self = SimpleNamespace(_media_card=media_card, _aux_card=aux_card)

    HomePage._refresh_chatmix_toggles(fake_self)

    aux_card.set_chatmix_toggle_visible.assert_called_once_with(False)


# ── HomePage: the update path when the user toggles a card's checkbox ──────

def test_on_chatmix_toggle_adds_the_channel(monkeypatch):
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_extra_channels = []
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    write_calls = []
    monkeypatch.setattr(saved, "write_to_file", lambda: write_calls.append(True))
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    fake_self = SimpleNamespace()
    HomePage._on_chatmix_toggle(fake_self, "media", True)

    assert saved.chatmix_extra_channels == ["media"]
    assert write_calls == [True]
    change_setting.assert_called_once_with("chatmix_extra_channels", ["media"])


def test_on_chatmix_toggle_removes_the_channel(monkeypatch):
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_extra_channels = ["media", "aux"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    monkeypatch.setattr(saved, "write_to_file", lambda: None)
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    fake_self = SimpleNamespace()
    HomePage._on_chatmix_toggle(fake_self, "media", False)

    assert saved.chatmix_extra_channels == ["aux"]
    change_setting.assert_called_once_with("chatmix_extra_channels", ["aux"])


def test_on_chatmix_toggle_survives_a_failed_daemon_notify(monkeypatch):
    """A failed D-Bus notify must not stop the local file write (mirrors
    _set_aux_enabled's independent try/except halves)."""
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_extra_channels = []
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    write_calls = []
    monkeypatch.setattr(saved, "write_to_file", lambda: write_calls.append(True))

    def _boom(*_a, **_kw):
        raise RuntimeError("no daemon running")

    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(_boom))

    fake_self = SimpleNamespace()
    HomePage._on_chatmix_toggle(fake_self, "aux", True)  # must not raise

    assert saved.chatmix_extra_channels == ["aux"]
    assert write_calls == [True]
