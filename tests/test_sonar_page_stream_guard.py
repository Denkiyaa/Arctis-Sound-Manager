# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Sonar page's Stream Guard bar.

The bar is the only user-facing control over what a Discord screen share is
allowed to carry, so its two jobs — reflecting the saved config on open, and
persisting every change immediately — are what these tests pin down. The guard
daemon picks the file up on its own; the widget never talks to it directly.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arctis_sound_manager.gui import sonar_page as sp  # noqa: E402
from arctis_sound_manager.stream_guard import GuardConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def guard_io(monkeypatch, qapp):
    """Redirect the bar's config I/O to memory; returns the saved-config list."""
    saved: list[GuardConfig] = []
    state = {"cfg": GuardConfig()}

    monkeypatch.setattr(sp, "load_guard_config", lambda: state["cfg"])
    monkeypatch.setattr(sp, "save_guard_config", lambda cfg: saved.append(cfg))
    return state, saved


def test_bar_reflects_saved_config(guard_io):
    state, _ = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game", "chat"))
    bar = sp._StreamGuardBar()
    assert bar._enable.isChecked()
    assert bar._boxes["game"].isChecked()
    assert bar._boxes["chat"].isChecked()
    assert not bar._boxes["media"].isChecked()


def test_checking_a_channel_saves_it(guard_io):
    state, saved = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game",))
    bar = sp._StreamGuardBar()
    saved.clear()

    bar._boxes["media"].setChecked(True)

    assert saved, "changing a channel must persist immediately"
    assert set(saved[-1].channels) == {"game", "media"}
    assert saved[-1].enabled is True


def test_unchecking_a_channel_saves_it(guard_io):
    state, saved = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game", "media"))
    bar = sp._StreamGuardBar()
    saved.clear()

    bar._boxes["media"].setChecked(False)

    assert set(saved[-1].channels) == {"game"}


def test_disabling_the_guard_is_saved_and_greys_out_channels(guard_io):
    state, saved = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game",))
    bar = sp._StreamGuardBar()
    saved.clear()

    bar._enable.setChecked(False)

    assert saved[-1].enabled is False
    assert not any(cb.isEnabled() for cb in bar._boxes.values()), (
        "channel picks are meaningless while the guard is off — they must be disabled"
    )


def test_channels_are_re_enabled_when_the_guard_comes_back(guard_io):
    state, _ = guard_io
    state["cfg"] = GuardConfig(enabled=False, channels=("game",))
    bar = sp._StreamGuardBar()
    assert not bar._boxes["game"].isEnabled()

    bar._enable.setChecked(True)
    assert all(cb.isEnabled() for cb in bar._boxes.values())


def test_disabled_guard_keeps_its_channel_selection(guard_io):
    """Turning the guard off must not silently forget which channels were
    picked — the user gets them back when they turn it on again."""
    state, saved = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game", "chat"))
    bar = sp._StreamGuardBar()
    saved.clear()

    bar._enable.setChecked(False)

    assert set(saved[-1].channels) == {"game", "chat"}


def test_save_failure_does_not_propagate(guard_io, monkeypatch):
    """A read-only config dir must not take the whole Sonar page down."""
    state, _ = guard_io
    state["cfg"] = GuardConfig(enabled=True, channels=("game",))
    bar = sp._StreamGuardBar()

    def _boom(_cfg):
        raise OSError("read-only file system")

    monkeypatch.setattr(sp, "save_guard_config", _boom)
    bar._boxes["media"].setChecked(True)  # must not raise
