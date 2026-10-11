# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The screen's EQ line follows the channel being listened to.

It used to read ``.sonar_preset_game`` unconditionally, so music on Media or
a call on Chat still showed the Game preset.
"""

from __future__ import annotations

import pytest

from arctis_sound_manager import oled_manager
from arctis_sound_manager.oled_manager import OledManager
from arctis_sound_manager.pw_utils import playing_channels


def _node(oid, mc, name, state="running"):
    return {"id": oid, "type": "PipeWire:Interface:Node",
            "info": {"state": state,
                     "props": {"media.class": mc, "node.name": name}}}


def _link(src, dst):
    return {"type": "PipeWire:Interface:Link",
            "info": {"output-node-id": src, "input-node-id": dst}}


_SINKS = [
    _node(1, "Audio/Sink", "Arctis_Game"),
    _node(2, "Audio/Sink", "Arctis_Chat"),
    _node(3, "Audio/Sink", "Arctis_Media"),
]


def test_running_app_stream_marks_its_channel():
    dump = _SINKS + [_node(10, "Stream/Output/Audio", "spotify"), _link(10, 3)]
    assert playing_channels(dump) == {"media"}


def test_app_on_external_device_is_output():
    dump = _SINKS + [
        _node(4, "Audio/Sink", "alsa_output.pci-0000_09_00.1.hdmi-stereo"),
        _node(5, "Audio/Sink", "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"),
        _node(10, "Stream/Output/Audio", "Microsoft Edge"), _link(10, 4),
        _node(11, "Stream/Output/Audio", "mpv"), _link(11, 5),
    ]
    # Straight into the headset is no channel at all.
    assert playing_channels(dump) == {"output"}


def test_paused_stream_does_not_count():
    dump = _SINKS + [_node(10, "Stream/Output/Audio", "firefox", "paused"),
                     _link(10, 3)]
    assert playing_channels(dump) == set()


def test_asm_internal_stream_does_not_count():
    # The loopback's own output stream is always running.
    dump = _SINKS + [_node(10, "Stream/Output/Audio", "Arctis_Game_sink_out"),
                     _link(10, 1)]
    assert playing_channels(dump) == set()


@pytest.fixture
def manager():
    m = object.__new__(OledManager)
    m._eq_channel = "game"
    return m


def _playing(monkeypatch, channels):
    monkeypatch.setattr(oled_manager, "playing_channels", lambda: channels)


def test_switches_to_the_channel_that_plays(manager, monkeypatch):
    _playing(monkeypatch, {"media"})
    assert manager._listened_eq_channel() == "media"


def test_current_channel_sticks_while_it_plays(manager, monkeypatch):
    manager._eq_channel = "media"
    _playing(monkeypatch, {"game", "media"})
    assert manager._listened_eq_channel() == "media"


def test_chat_alone_is_shown(manager, monkeypatch):
    _playing(monkeypatch, {"chat"})
    assert manager._listened_eq_channel() == "chat"


def test_chat_never_wins_over_another_channel(manager, monkeypatch):
    # In a Discord call, then a game starts: the game's EQ is shown.
    manager._eq_channel = "chat"
    _playing(monkeypatch, {"game", "chat"})
    assert manager._listened_eq_channel() == "game"


def test_last_channel_kept_when_nothing_plays(manager, monkeypatch):
    manager._eq_channel = "media"
    _playing(monkeypatch, set())
    assert manager._listened_eq_channel() == "media"
    _playing(monkeypatch, None)
    assert manager._listened_eq_channel() == "media"
