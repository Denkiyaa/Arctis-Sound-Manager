# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The microphone chain must never be fed by an output.

Observed on a Nova 7 Gen 2 sitting on an iec958-stereo profile, which exposes
no capture node of its own: the daemon stored the *sink* name as the device's
microphone (``physical_in or fallback``, where the fallback was the output),
and ensure_micro_capture_link then linked that sink's monitor into
``effect_input.sonar-micro-eq``. Everything the user could hear went out as
their microphone — a browser tab played over Discord as if they were speaking
it — with nothing in the UI or the log saying so.

Two independent guards are pinned here, because the failure is silent to the
person making it: the daemon must not store an output as the mic, and the link
layer must refuse one even if something else asks.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager import pw_utils

SINK = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.iec958-stereo"
MIC = "alsa_input.usb-HP__Inc_HyperX_DuoCast_202011110001-00.analog-stereo"
CAPTURE = "effect_input.sonar-micro-eq"


def _node(node_id: int, name: str, media_class: str) -> dict:
    return {"id": node_id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name, "media.class": media_class}}}


def _port(port_id: int, node_id: int, direction: str, channel: str) -> dict:
    return {"id": port_id, "type": "PipeWire:Interface:Port",
            "info": {"props": {"port.direction": direction,
                               "node.id": node_id,
                               "audio.channel": channel,
                               "port.name": f"{direction}_{channel}"}}}


@pytest.fixture
def graph():
    """A sink, a real microphone, and the ASM mic-EQ capture node."""
    return [
        _node(101, SINK, "Audio/Sink"),
        _port(1011, 101, "out", "FL"),      # the sink's monitor
        _port(1012, 101, "out", "FR"),
        _node(100, MIC, "Audio/Source"),
        _port(1001, 100, "out", "FL"),
        _port(1002, 100, "out", "FR"),
        _node(200, CAPTURE, "Stream/Input/Audio"),
        _port(2001, 200, "in", "FL"),
        _port(2002, 200, "in", "FR"),
    ]


# ── the link layer ────────────────────────────────────────────────────────────

@pytest.fixture
def pw_calls(monkeypatch):
    """Record what would have been run against the graph, and run nothing."""
    import types

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pw_utils, "_pw_run", fake_run)
    return calls


def test_a_sink_is_refused_as_a_microphone(graph, pw_calls):
    """Its output ports are its monitor — this is desktop audio, not a mic."""
    assert pw_utils.ensure_capture_link(SINK, CAPTURE, data=graph) is False
    assert pw_calls == []


def test_a_real_microphone_is_still_linked(graph, pw_calls):
    """The guard must not cost the feature it protects."""
    assert pw_utils.ensure_capture_link(MIC, CAPTURE, data=graph) is True
    assert len([c for c in pw_calls if "-d" not in c]) == 2


# ── the daemon's device state ─────────────────────────────────────────────────

def test_a_headset_with_no_capture_node_reports_no_microphone():
    """The daemon used to fall back to the output here. Empty is the honest
    answer: the watchdog links the mic when a real source turns up."""
    import inspect

    from arctis_sound_manager import core

    source = inspect.getsource(core.CoreEngine)
    assert "physical_in=physical_in or fallback" not in source, (
        "physical_in must not fall back to the output sink — that is what fed "
        "the microphone chain with the headset's monitor")
    assert 'physical_in=physical_in or ""' in source
