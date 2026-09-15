# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for HomePage._friendly_app_name — the per-channel app label helper.

Discord's audio streams report application.name "WEBRTC VoiceEngine"; the
helper must fall back to the process binary so the user sees "Discord".
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.home_page import HomePage


def test_uses_application_name_when_specific():
    assert HomePage._friendly_app_name({"application.name": "Firefox"}) == "Firefox"


def test_falls_back_to_binary_for_webrtc():
    pl = {
        "application.name": "WEBRTC VoiceEngine",
        "application.process.binary": "Discord",
    }
    assert HomePage._friendly_app_name(pl) == "Discord"


def test_capitalizes_and_strips_path_from_binary():
    pl = {"application.name": "", "application.process.binary": "/usr/bin/mpv"}
    assert HomePage._friendly_app_name(pl) == "Mpv"


def test_keeps_application_name_over_binary_when_not_generic():
    pl = {
        "application.name": "Spotify",
        "application.process.binary": "spotify",
    }
    assert HomePage._friendly_app_name(pl) == "Spotify"


def test_default_when_nothing_usable():
    assert HomePage._friendly_app_name({}) == "Audio"


# ── _on_stream_drop override key (issue #108) ──────────────────────────────
# The GUI must persist manual moves under the SAME key video_router.py uses,
# i.e. app_override_key(application.name, binary), not the friendly label.
# Otherwise two "Chromium" Electron apps (Vesktop / Pear Desktop) collide and
# their channels do not survive a restart.

from types import SimpleNamespace

from arctis_sound_manager.gui import home_page as _hp


class _FakeSink:
    def __init__(self, index, name):
        self.index = index
        self.name = name


class _FakeSinkInput:
    def __init__(self, index, proplist):
        self.index = index
        self.proplist = proplist


class _FakePulse:
    def __init__(self, sinks, sink_inputs):
        self._sinks = sinks
        self._sink_inputs = sink_inputs
        self.moved = []

    def sink_list(self):
        return self._sinks

    def sink_input_list(self):
        return self._sink_inputs

    def sink_input_move(self, si_index, target_index):
        self.moved.append((si_index, target_index))


def _drop_and_capture(monkeypatch, proplist, si_index=42, target="Arctis_Chat"):
    saved = {}
    monkeypatch.setattr(_hp, "_load_overrides", lambda: {})
    monkeypatch.setattr(_hp, "_save_overrides", lambda o: saved.update(o))

    pulse = _FakePulse(
        sinks=[_FakeSink(7, target)],
        sink_inputs=[_FakeSinkInput(si_index, proplist)],
    )
    fake_self = SimpleNamespace(_get_pulse=lambda: pulse)
    HomePage._on_stream_drop(fake_self, si_index, "friendly-label", 123, target)
    return saved, pulse


def test_on_stream_drop_uses_composite_key_for_generic_app(monkeypatch):
    saved, pulse = _drop_and_capture(
        monkeypatch,
        {"application.name": "Chromium", "application.process.binary": "vesktop"},
    )
    assert saved == {"Chromium|vesktop": "Arctis_Chat"}
    assert pulse.moved == [(42, 7)]


def test_on_stream_drop_separates_two_chromium_apps(monkeypatch):
    saved_a, _ = _drop_and_capture(
        monkeypatch,
        {"application.name": "Chromium", "application.process.binary": "vesktop"},
    )
    saved_b, _ = _drop_and_capture(
        monkeypatch,
        {"application.name": "Chromium", "application.process.binary": "electron"},
    )
    # Distinct keys → the two apps no longer overwrite each other.
    assert "Chromium|vesktop" in saved_a
    assert "Chromium|electron" in saved_b
    assert set(saved_a) != set(saved_b)


def test_on_stream_drop_keeps_plain_name_for_specific_app(monkeypatch):
    saved, _ = _drop_and_capture(
        monkeypatch,
        {"application.name": "Firefox", "application.process.binary": "firefox"},
    )
    assert saved == {"Firefox": "Arctis_Chat"}


def test_on_stream_drop_falls_back_to_label_without_sink_input(monkeypatch):
    # Native stream: no matching PA sink-input for the id → keep old behaviour.
    saved = {}
    monkeypatch.setattr(_hp, "_load_overrides", lambda: {})
    monkeypatch.setattr(_hp, "_save_overrides", lambda o: saved.update(o))
    pulse = _FakePulse(sinks=[_FakeSink(7, "Arctis_Chat")], sink_inputs=[])
    fake_self = SimpleNamespace(_get_pulse=lambda: pulse)
    HomePage._on_stream_drop(fake_self, 99, "Mpv", 123, "Arctis_Chat")
    assert saved == {"Mpv": "Arctis_Chat"}


# ── _update_native_apps must never surface ASM's own nodes as an app tag ────
# get_native_streams() falls back to raw node.name for a stream with neither
# application.name nor application.process.binary set — exactly ASM's own
# filter-chain/loopback nodes. If one is (even transiently) linked to a
# channel's virtual sink, it must not show up as if a real app were dragged
# onto that channel.

def _fake_native_stream(node_name, sink_name, *, app_props=None, sid=1, pid="0"):
    props = {"node.name": node_name}
    if app_props:
        props.update(app_props)
    return {
        "id": sid,
        "app_name": app_props.get("application.name", node_name) if app_props else node_name,
        "pid": pid,
        "sink_name": sink_name,
        "sink_id": 7,
        "props": props,
    }


def _fake_home_page_for_native_apps(native_cache):
    return SimpleNamespace(
        _native_cache=native_cache,
        _game_card=object(),
        _chat_card=object(),
        _media_card=object(),
        _aux_card=object(),
        _is_asm_internal_node=HomePage._is_asm_internal_node,
    )


def test_update_native_apps_excludes_asm_internal_node_from_media_card():
    fake_self = _fake_home_page_for_native_apps([
        _fake_native_stream(
            "effect_output.virtual-surround-7.1-hesuvi-aux", "Arctis_Media", sid=1,
        ),
        _fake_native_stream(
            "some-edge-node", "Arctis_Media", sid=2,
            app_props={"application.name": "Microsoft Edge"}, pid="777",
        ),
    ])

    per_card = HomePage._update_native_apps(fake_self, pulse_sinks=[], rescan=False)

    media_rows = per_card.get(id(fake_self._media_card), [])
    app_names = [row[0] for row in media_rows]
    assert "Microsoft Edge" in app_names
    assert "effect_output.virtual-surround-7.1-hesuvi-aux" not in app_names
    assert len(media_rows) == 1


def test_update_native_apps_excludes_loopback_playback_node():
    fake_self = _fake_home_page_for_native_apps([
        _fake_native_stream("Arctis_Media_sink_out", "Arctis_Media", sid=3),
    ])

    per_card = HomePage._update_native_apps(fake_self, pulse_sinks=[], rescan=False)

    assert not per_card.get(id(fake_self._media_card))
