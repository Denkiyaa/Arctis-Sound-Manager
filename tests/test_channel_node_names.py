# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_channel_node_names.py — the Output channel must name its headset.

Game, Chat and Media appear in system audio pickers as "<headset> Game" and so
on. The Output channel's node appeared as "Sonar Output EQ": no mention of the
headset, sitting among three siblings that all carry its name, which reads as
an unrelated technical entry (#146).

It is the only one of the four EQ nodes that is user-visible — the others are
declared Audio/Sink/Internal — so it is the only one whose label matters here.
"""
from __future__ import annotations

from arctis_sound_manager import device_state
from arctis_sound_manager.sonar_to_pipewire import _channel_node_description


def test_output_channel_names_the_headset(monkeypatch):
    monkeypatch.setattr(device_state, "get_device_name",
                        lambda: "Arctis Nova 7P Gen 2")

    assert _channel_node_description("output") == "Arctis Nova 7P Gen 2 Output"


def test_output_falls_back_when_no_device_is_known(monkeypatch):
    """Never produce a dangling label like " Output"."""
    monkeypatch.setattr(device_state, "get_device_name", lambda: "")

    assert _channel_node_description("output") == "Arctis Output"


def test_internal_channels_keep_their_label(monkeypatch):
    """These never reach a picker; renaming them would only churn configs."""
    monkeypatch.setattr(device_state, "get_device_name", lambda: "Whatever")

    assert _channel_node_description("game") == "Sonar Game EQ"
    assert _channel_node_description("chat") == "Sonar Chat EQ"
    assert _channel_node_description("media") == "Sonar Media EQ"
