# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the "other applications" area on the Channels page.

Why it exists: the four cards only list streams sitting on their own sink, so
an application playing anywhere else is invisible in ASM and cannot be routed
from it. That happens whenever the system default is not a channel, most
notably the headset's own hardware device, which is a sensible default for
anyone using a Nova Pro dock whose AUX output feeds speakers. Audio is
audible, nothing looks broken, and yet the mixer appears empty. Reported on
Discord by autune.

These tests pin the selection rules, since getting them wrong either hides the
applications this area exists for, or floods it with ASM's own plumbing.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from arctis_sound_manager.gui.home_page import HomePage


def _sink(index: int, name: str, description: str = ""):
    return SimpleNamespace(index=index, name=name, description=description or name)


def _stream(index: int, sink: int, app: str, binary: str = "", media: str = "", pid: int = 0):
    return SimpleNamespace(index=index, sink=sink, proplist={
        "application.name": app,
        "application.process.binary": binary,
        "application.process.id": str(pid),
        "media.name": media,
    })


# The topology from the report: virtual channels exist, but the default output
# is the headset hardware, so streams land there instead of on a channel.
_SINKS = [
    _sink(1, "Arctis_Game", "Arctis Game"),
    _sink(2, "Arctis_Chat", "Arctis Chat"),
    _sink(3, "Arctis_Media", "Arctis Media"),
    _sink(4, "effect_input.sonar-game-eq", "Sonar Game EQ"),
    _sink(9, "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo",
          "Arctis Nova Pro Wireless Analog Stereo"),
    _sink(10, "alsa_output.pci-0000_01_00.1.hdmi-stereo", "HDMI 2"),
]


def _page() -> HomePage:
    page = HomePage.__new__(HomePage)
    page._hidden_apps = set()
    return page


def test_stream_on_headset_hardware_is_listed():
    """The case this area was built for: audible, but on no channel."""
    page = _page()
    rows = page._collect_unassigned([_stream(50, 9, "Firefox", "firefox")], _SINKS, None)
    assert [r["label"] for r in rows] == ["Firefox"]
    assert rows[0]["where"] == "Arctis Nova Pro Wireless Analog Stereo"


def test_streams_already_on_a_card_are_not_listed():
    """Anything a card shows must not appear twice."""
    page = _page()
    streams = [
        _stream(51, 1, "Game", "game"),
        _stream(52, 3, "mpv", "mpv"),
        _stream(53, 4, "OnEqNode", "app"),
    ]
    assert page._collect_unassigned(streams, _SINKS, None) == []


def test_external_output_card_streams_are_not_listed():
    """When an Output device is configured, its card already lists them."""
    page = _page()
    hdmi = _SINKS[-1]
    streams = [_stream(54, 10, "Kodi", "kodi")]
    assert page._collect_unassigned(streams, _SINKS, hdmi) == []
    # Without that card configured, the same stream is unassigned.
    assert len(page._collect_unassigned(streams, _SINKS, None)) == 1


def test_asm_internal_plumbing_is_never_offered():
    """ASM's own loopbacks and filter chains appear as streams too.

    Listing them would bury the user's actual applications under machinery
    they neither recognise nor should ever move.
    """
    page = _page()
    streams = [
        _stream(60, 9, "pw-loopback", "pw-loopback"),
        _stream(61, 9, "PipeWire", "pipewire"),
        _stream(62, 9, "Arctis_Media", "pw-loopback"),
        _stream(63, 9, "SomeApp", "someapp", media="Virtual Surround Sink"),
        _stream(64, 9, "SomeApp2", "someapp2", media="EQ output"),
        _stream(65, 9, "", ""),
    ]
    assert page._collect_unassigned(streams, _SINKS, None) == []


def test_dismissed_apps_disappear_from_the_list():
    page = _page()
    streams = [_stream(70, 9, "Firefox", "firefox"), _stream(71, 9, "mpv", "mpv")]
    rows = page._collect_unassigned(streams, _SINKS, None)
    assert len(rows) == 2

    page._hidden_apps.add(rows[0]["key"])
    remaining = page._collect_unassigned(streams, _SINKS, None)
    assert [r["label"] for r in remaining] == ["mpv"]


def test_one_row_per_application_not_per_stream():
    """A browser with several tabs playing is one entry, not five."""
    page = _page()
    streams = [_stream(80 + i, 9, "Firefox", "firefox") for i in range(5)]
    assert len(page._collect_unassigned(streams, _SINKS, None)) == 1


def test_apps_sharing_a_generic_name_are_kept_apart():
    """Every Electron app reports "Chromium" (issue #108).

    Dismissing one must not silently hide the others, so entries are keyed on
    name plus binary rather than name alone.
    """
    page = _page()
    streams = [
        _stream(90, 9, "Chromium", "vesktop"),
        _stream(91, 9, "Chromium", "pear-desktop"),
    ]
    rows = page._collect_unassigned(streams, _SINKS, None)
    assert len({r["key"] for r in rows}) == 2

    page._hidden_apps.add(rows[0]["key"])
    assert len(page._collect_unassigned(streams, _SINKS, None)) == 1


def test_dismissing_persists_and_never_moves_audio():
    """Dismissing answers "I know where this plays", nothing more."""
    page = _page()
    page._poll_volumes = lambda: None
    saved: list[set] = []
    with patch("arctis_sound_manager.gui.home_page._save_hidden_apps", saved.append):
        page._on_dismiss_app("Firefox|firefox")
    assert saved == [{"Firefox|firefox"}]
    assert page._hidden_apps == {"Firefox|firefox"}


def test_show_hidden_restores_every_dismissed_app():
    page = _page()
    page._poll_volumes = lambda: None
    page._hidden_apps = {"Firefox|firefox", "mpv|mpv"}
    with patch("arctis_sound_manager.gui.home_page._save_hidden_apps"):
        page._on_unhide_all()
    assert page._hidden_apps == set()
