# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the audio-graph section of the bug report.

Why this section exists, and why it is worth locking down: issue #180 (a
headset that never reaches its inactivity timeout) took two wrong diagnoses,
both because the report showed sink names without node *states* and without
*links*. "Something keeps the device awake" is not actionable; "the HeSuVi
output holds it" is. These tests pin the three properties that made the
difference, so a later refactor of the report cannot quietly drop them again.
"""
from __future__ import annotations

from arctis_sound_manager.bug_reporter import (
    _alsa_pcm_state,
    _arctis_pw_nodes,
    _audio_graph,
    _is_asm_node,
)


def _node(nid: int, name: str, mclass: str, state: str, **props) -> dict:
    return {
        'id': nid,
        'type': 'PipeWire:Interface:Node',
        'info': {'state': state, 'props': {'node.name': name, 'media.class': mclass, **props}},
    }


def _link(lid: int, out_id: int, in_id: int, state: str = 'active') -> dict:
    return {
        'id': lid,
        'type': 'PipeWire:Interface:Link',
        'info': {'output-node-id': out_id, 'input-node-id': in_id, 'state': state},
    }


def _graph() -> list[dict]:
    """The #180 topology: everything idle except the chain feeding the device."""
    return [
        _node(66, 'alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo', 'Audio/Sink', 'running'),
        _node(72, 'alsa_output.pci-0000_00_1f.3.analog-stereo', 'Audio/Sink', 'running'),
        _node(241, 'Arctis_Game', 'Audio/Sink', 'idle'),
        _node(242, 'Arctis_Game_sink_out', 'Stream/Output/Audio', 'idle',
              **{'target.object': 'effect_input.sonar-game-eq', 'node.pause-on-idle': 'true'}),
        _node(94, 'effect_input.sonar-game-eq', 'Audio/Sink/Internal', 'idle'),
        _node(95, 'effect_output.sonar-game-eq', 'Stream/Output/Audio', 'running',
              **{'node.linger': 'true'}),
        _node(103, 'effect_output.virtual-surround-7.1-hesuvi', 'Stream/Output/Audio', 'running'),
        _node(900, 'some-video-node', 'Video/Source', 'idle'),
        _link(500, 242, 94),
        _link(501, 95, 103),
        _link(502, 103, 66),
    ]


# ── node states ──────────────────────────────────────────────────────────────

def test_node_state_is_reported():
    """The single most important field, and the one that was missing."""
    text = _audio_graph(_graph())
    assert 'running' in text and 'idle' in text
    device_line = next(l for l in text.splitlines()
                       if 'alsa_output.usb-SteelSeries' in l)
    assert 'running' in device_line


def test_stream_output_nodes_are_not_dropped():
    """`Stream/Output/Audio` is the class of every node that feeds a device.

    A `media.class.startswith("Audio")` filter silently drops all of them,
    which would omit exactly the nodes this section exists to show.
    """
    text = _audio_graph(_graph())
    for name in ('Arctis_Game_sink_out', 'effect_output.sonar-game-eq',
                 'effect_output.virtual-surround-7.1-hesuvi'):
        assert name in text, f'{name} missing from the graph section'


def test_non_audio_nodes_are_excluded():
    assert 'some-video-node' not in _audio_graph(_graph())


# ── links ────────────────────────────────────────────────────────────────────

def test_links_name_both_ends():
    """Knowing a device is held is useless without knowing by what."""
    text = _audio_graph(_graph())
    assert ('effect_output.virtual-surround-7.1-hesuvi  ->  '
            'alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo') in text


def test_empty_graph_says_so_rather_than_looking_healthy():
    text = _audio_graph([])
    assert 'no audio nodes' in text
    assert 'no links' in text


def test_unavailable_dump_is_explicit():
    """Absent data must never be indistinguishable from an empty graph."""
    assert 'unavailable' in _audio_graph(None)


# ── ASM ownership marker ─────────────────────────────────────────────────────

def test_physical_device_is_not_flagged_as_an_asm_node():
    """The headset's own sink is named `alsa_output.usb-SteelSeries_Arctis_7…`
    and matches the `Arctis_` fragment. Flagging it as ours would confuse the
    one comparison this section is for."""
    assert not _is_asm_node('alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo')
    assert not _is_asm_node('bluez_output.AA_BB_CC.a2dp-sink')
    assert _is_asm_node('Arctis_Game')
    assert _is_asm_node('effect_output.virtual-surround-7.1-hesuvi')

    text = _audio_graph(_graph())
    device_line = next(l for l in text.splitlines() if 'alsa_output.usb-SteelSeries' in l)
    assert '<-- ASM' not in device_line
    asm_line = next(l for l in text.splitlines() if l.rstrip().endswith('Arctis_Game <-- ASM'))
    assert asm_line


# ── routing props ────────────────────────────────────────────────────────────

def test_routing_props_shown_for_asm_nodes_only():
    """Whether the on-disk config reached the running graph (#100, #102, #180)."""
    text = _audio_graph(_graph())
    sink_out = next(l for l in text.splitlines() if 'Arctis_Game_sink_out' in l)
    assert 'target.object=effect_input.sonar-game-eq' in sink_out
    assert 'node.pause-on-idle=true' in sink_out
    # The device carries plenty of props too; printing them would bury the rest.
    device_line = next(l for l in text.splitlines() if 'alsa_output.pci-' in l)
    assert '[' not in device_line


# ── Arctis node list keeps its state field ───────────────────────────────────

def test_arctis_node_list_includes_state():
    text = _arctis_pw_nodes(_graph())
    assert 'state=' in text
    assert 'state=running' in text


# ── kernel view ──────────────────────────────────────────────────────────────

def test_alsa_pcm_state_never_raises():
    """Runs on machines with no /proc/asound at all (containers, CI)."""
    result = _alsa_pcm_state()
    assert isinstance(result, str) and result
