# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for stream_guard — which links into a screen-share capture survive.

Discord links every playback stream into its own ``discord_capture`` node, so
the guard has to decide per link whether the source belongs to an allowed
Arctis channel. The rule is structural, not name-based: a source is allowed
when it also feeds one of the allowed channel sinks.
"""
from __future__ import annotations

import json

import pytest

from arctis_sound_manager.stream_guard import (CHANNEL_SINKS, GuardConfig,
                                               describe_cut, links_to_cut,
                                               load_config, save_config)


# ── pw-dump fixture builders ──────────────────────────────────────────────

def _node(node_id: int, name: str) -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {"props": {"node.name": name}},
    }


def _link(link_id: int, out_node: int, in_node: int) -> dict:
    return {
        "id": link_id,
        "type": "PipeWire:Interface:Link",
        "info": {"props": {
            "link.output.node": out_node,
            "link.input.node": in_node,
        }},
    }


# Node ids mirroring the real graph: three Arctis channels, a capture node
# Discord created, and three apps sitting on different channels.
CAPTURE, GAME, CHAT, MEDIA = 100, 10, 11, 12
OBS, CHROME, VOICE = 20, 21, 22

GAME_ONLY = set(CHANNEL_SINKS["game"])


def _graph(*links: dict, with_capture: bool = True) -> list:
    nodes = [
        _node(GAME, "Arctis_Game"),
        _node(CHAT, "Arctis_Chat"),
        _node(MEDIA, "Arctis_Media"),
        _node(OBS, "OBS-Monitor"),
        _node(CHROME, "Google Chrome"),
        _node(VOICE, "WEBRTC VoiceEngine"),
    ]
    if with_capture:
        nodes.append(_node(CAPTURE, "discord_capture"))
    return [*nodes, *links]


# ── links_to_cut ──────────────────────────────────────────────────────────

def test_no_capture_node_means_nothing_to_cut():
    """The common case: no screen share running, guard must be inert."""
    dump = _graph(
        _link(1, OBS, GAME),
        _link(2, CHROME, MEDIA),
        with_capture=False,
    )
    assert links_to_cut(dump, GAME_ONLY) == []


def test_source_on_allowed_channel_survives():
    dump = _graph(
        _link(1, OBS, GAME),        # OBS plays on the Game channel
        _link(2, OBS, CAPTURE),     # …and Discord captures it
    )
    assert links_to_cut(dump, GAME_ONLY) == []


def test_source_on_another_channel_is_cut():
    dump = _graph(
        _link(1, CHROME, MEDIA),    # Chrome plays on Media
        _link(2, CHROME, CAPTURE),  # Discord grabbed it anyway
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_chat_channel_is_cut_so_the_call_does_not_echo_itself():
    """Discord's own voice output must never be fed back into the share."""
    dump = _graph(
        _link(1, VOICE, CHAT),
        _link(2, VOICE, CAPTURE),
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_only_capture_links_are_touched():
    """A stream on a disallowed channel keeps playing — we cut its capture
    link, never its link to the sink the user actually listens on."""
    dump = _graph(
        _link(1, CHROME, MEDIA),
        _link(2, CHROME, CAPTURE),
        _link(3, OBS, GAME),
        _link(4, OBS, CAPTURE),
    )
    assert links_to_cut(dump, GAME_ONLY) == [2]


def test_monitor_of_allowed_sink_survives():
    """Capture reading the channel sink's own monitor is the allowed case."""
    dump = _graph(_link(1, GAME, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == []


def test_monitor_of_disallowed_sink_is_cut():
    dump = _graph(_link(1, MEDIA, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_filter_chain_input_counts_as_the_same_channel():
    """With the Sonar EQ on, a stream sits on effect_input.sonar-game-eq rather
    than Arctis_Game. The user means the same channel, so it must survive."""
    eq_game = 30
    dump = [
        _node(CAPTURE, "discord_capture"),
        _node(eq_game, "effect_input.sonar-game-eq"),
        _node(OBS, "OBS-Monitor"),
        _link(1, OBS, eq_game),
        _link(2, OBS, CAPTURE),
    ]
    assert links_to_cut(dump, GAME_ONLY) == []


def test_unrouted_source_is_cut():
    """A stream on no Arctis channel at all (e.g. straight to the hardware
    sink) has not been opted in, so it does not go on the stream."""
    stray = 40
    dump = _graph(
        _link(1, stray, CAPTURE),
    ) + [_node(stray, "some-notification-sound")]
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_multiple_channels_allowed():
    dump = _graph(
        _link(1, CHROME, MEDIA),
        _link(2, CHROME, CAPTURE),
        _link(3, VOICE, CHAT),
        _link(4, VOICE, CAPTURE),
    )
    allowed = set(CHANNEL_SINKS["game"]) | set(CHANNEL_SINKS["media"])
    assert links_to_cut(dump, allowed) == [4]


def test_empty_allowlist_cuts_everything():
    dump = _graph(
        _link(1, OBS, GAME),
        _link(2, OBS, CAPTURE),
        _link(3, CHROME, CAPTURE),
    )
    assert links_to_cut(dump, set()) == [2, 3]


def test_malformed_objects_are_skipped_not_fatal():
    """pw-dump can carry objects mid-teardown with info: null."""
    dump = _graph(_link(1, CHROME, CAPTURE)) + [
        {"id": 900, "type": "PipeWire:Interface:Link", "info": None},
        {"id": 901, "type": "PipeWire:Interface:Node", "info": None},
        {"id": 902, "type": "PipeWire:Interface:Port"},
    ]
    assert links_to_cut(dump, GAME_ONLY) == [1]


def test_capture_self_link_is_left_alone():
    dump = _graph(_link(1, CAPTURE, CAPTURE))
    assert links_to_cut(dump, GAME_ONLY) == []


def test_alternate_capture_node_name_is_policed():
    dump = [
        _node(CAPTURE, "vesktop_capture"),
        _node(CHROME, "Google Chrome"),
        _link(1, CHROME, CAPTURE),
    ]
    assert links_to_cut(dump, GAME_ONLY, capture_nodes=("vesktop_capture",)) == [1]


# ── describe_cut ──────────────────────────────────────────────────────────

def test_describe_cut_names_both_ends():
    dump = _graph(_link(7, CHROME, CAPTURE))
    assert describe_cut(dump, [7]) == ["Google Chrome -> discord_capture"]


def test_describe_cut_survives_unknown_link_id():
    assert describe_cut(_graph(), [999]) == ["#None -> #None"]


# ── GuardConfig ───────────────────────────────────────────────────────────

def test_allowed_sinks_expands_channels():
    cfg = GuardConfig(channels=("game",))
    assert cfg.allowed_sinks() == {"Arctis_Game", "effect_input.sonar-game-eq"}


def test_unknown_channel_names_are_dropped():
    cfg = GuardConfig(channels=("game", "bogus"))
    assert cfg.channels == ("game",)


def test_roundtrip(tmp_path):
    path = tmp_path / "stream_guard.json"
    save_config(GuardConfig(enabled=False, channels=("game", "media")), path)
    assert load_config(path) == GuardConfig(enabled=False, channels=("game", "media"))


def test_missing_config_defaults_to_enabled_game_only(tmp_path):
    """Fail closed: no config must not mean 'broadcast everything'."""
    cfg = load_config(tmp_path / "absent.json")
    assert cfg.enabled is True
    assert cfg.channels == ("game",)


@pytest.mark.parametrize("body", ["{ not json", "[]", '{"channels": 5}'])
def test_corrupt_config_falls_back_to_guarding(tmp_path, body):
    path = tmp_path / "stream_guard.json"
    path.write_text(body)
    cfg = load_config(path)
    assert cfg.enabled is True
    assert cfg.channels == ("game",)


def test_save_is_atomic_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "stream_guard.json"
    save_config(GuardConfig(), path)
    assert json.loads(path.read_text()) == {"enabled": True, "channels": ["game"]}
    assert list(tmp_path.iterdir()) == [path]


# ── idle cost: the guard must not snapshot the graph when nothing captures it ─
#
# The first version re-dumped the whole graph every 5 s regardless, which cost
# ~12 % CPU around the clock on a machine that was not sharing anything — more
# than the polling loop the event-driven design was meant to replace. The
# safety net now backs off while no capture node exists, which is only safe as
# long as the two predicates below keep telling the truth: `capture_node_present`
# decides the cadence, and `_mentions_capture_node` is what wakes the guard when
# a share starts. If either silently stops matching, the guard costs nothing and
# does nothing — the worst of both.

from arctis_sound_manager.scripts.stream_guard import (  # noqa: E402
    _mentions_capture_node, capture_node_present)


def test_capture_node_present_detects_the_node():
    assert capture_node_present(_graph(_link(1, OBS, CAPTURE))) is True


def test_capture_node_present_false_without_one():
    assert capture_node_present(_graph(_link(1, OBS, GAME), with_capture=False)) is False


def test_capture_node_present_ignores_similar_names():
    dump = [_node(CAPTURE, "discord_capture_something_else"), _node(OBS, "OBS-Monitor")]
    assert capture_node_present(dump) is False


def test_mentions_capture_node_spots_a_starting_share():
    """PipeWire names the node when Discord creates it — that is the wake-up."""
    chunk = (b'  added:\n\tid: 214\n\ttype: PipeWire:Interface:Node/3\n'
             b'\t\tnode.name = "discord_capture"\n\t\tmedia.class = "Audio/Sink"\n')
    assert _mentions_capture_node(chunk) is True


def test_mentions_capture_node_ignores_ordinary_churn():
    """Volume moves and stream starts must not wake the guard on an idle graph."""
    chunk = (b'  changed:\n\tid: 96\n\ttype: PipeWire:Interface:Node/3\n'
             b'\t\tnode.name = "Google Chrome"\n\t\tchannelVolumes: [ 0.5, 0.5 ]\n')
    assert _mentions_capture_node(chunk) is False


def test_mentions_capture_node_handles_an_empty_burst():
    assert _mentions_capture_node(b"") is False
