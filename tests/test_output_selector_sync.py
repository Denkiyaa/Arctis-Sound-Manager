# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""One output device, one source of truth.

Three places let the user pick the Output channel's device: the Settings page,
the Channels tab's Output card, and the Equalizer's selector. The first two
write ``external_output_device``, which is also what the daemon reads to aim
the Output chain. The selector resolved purely from ``OutputMemory``, a file
nothing else writes — so a device chosen anywhere else never reached it, and
the two disagreed with no sign that they had.

Worse, the two sides did not even name devices the same way: the Channels card
saves a ``node.nick``, the selector keys its combo on ``node.name``. So even
when a value did travel, it could fail to match.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
HDMI = "alsa_output.pci-0000_09_00.1.hdmi-stereo"


class _FakeSink:
    def __init__(self, name, nick=""):
        self.name = name
        self.description = name
        self.proplist = {"node.nick": nick} if nick else {}


def _selector(tmp_path, monkeypatch, *, configured: str | None, sinks,
              memory_prefers: str = HDMI):
    """An OutputSelector wired to a throwaway settings file and sink list."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from arctis_sound_manager.gui import output_selector as osel

    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    if configured is not None:
        (settings_dir / "general_settings.yaml").write_text(
            f"external_output_device: {configured}\n")
    monkeypatch.setattr("arctis_sound_manager.constants.SETTINGS_FOLDER", settings_dir)

    pulse = MagicMock()
    pulse.__enter__ = lambda s: s
    pulse.__exit__ = lambda *a: False
    pulse.sink_list.return_value = sinks
    fake_pulsectl = types.ModuleType("pulsectl")
    fake_pulsectl.Pulse = lambda *a, **kw: pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake_pulsectl)

    # The memory prefers HDMI, so a selector that ignores the settings shows
    # HDMI — which is precisely the bug these tests pin down.
    memory = MagicMock()
    memory.resolve.return_value = memory_prefers
    memory.is_fallback.return_value = False
    memory.preferred = memory_prefers
    monkeypatch.setattr(osel.OutputMemory, "load", staticmethod(lambda: memory))

    with patch.object(osel.OutputSelector, "_available",
                      lambda self: [(s.name, s.name) for s in sinks]):
        yield_widget = osel.OutputSelector()
    return yield_widget


def test_the_selector_shows_what_settings_configured(tmp_path, monkeypatch):
    """A device chosen from Settings or the Channels tab must reach this combo."""
    sel = _selector(tmp_path, monkeypatch,
                    configured=HEADSET,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HEADSET, "the selector ignored external_output_device"


def test_a_nick_saved_by_the_channels_card_still_matches(tmp_path, monkeypatch):
    """The Channels card saves node.nick; this combo is keyed on node.name.

    Without matching both, the value travelled and then failed to resolve —
    the selector fell back to its own memory and the two views disagreed.
    """
    sel = _selector(tmp_path, monkeypatch,
                    configured="Q95A",
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI, nick="Q95A")],
                    memory_prefers=HEADSET)   # so only a real match can win
    assert sel._current == HDMI


def test_an_incoming_change_is_not_echoed_back(tmp_path, monkeypatch):
    """Adopting the configured device must not re-emit it.

    Every emission ends in a filter-chain restart. A change made in Settings is
    already applied — the daemon aims the Output chain from the same value — so
    echoing it would interrupt audio each time the device is changed elsewhere.
    """
    emitted = []
    sel = _selector(tmp_path, monkeypatch,
                    configured=HEADSET,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    sel.target_changed.connect(emitted.append)

    sel._current = None          # force the change branch on the next pass
    sel.refresh()

    assert sel._current == HEADSET
    assert emitted == [], "a settings-driven change was sent straight back out"


def test_the_memory_still_covers_a_device_that_is_gone(tmp_path, monkeypatch):
    """The fallback ladder keeps its job: configured device absent from the
    graph means the memory decides, which is what it exists for."""
    sel = _selector(tmp_path, monkeypatch,
                    configured="bluez_output.30_96_10_49_54_E2.1",
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HDMI     # the memory's answer
    assert sel._on_configured is False


def test_nothing_configured_falls_back_to_the_memory(tmp_path, monkeypatch):
    sel = _selector(tmp_path, monkeypatch,
                    configured=None,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HDMI
