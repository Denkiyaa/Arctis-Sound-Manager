# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recreating the Game/Media loopback gives it a new node id, orphaning the
streams that had been moved onto it (#304). They must be moved back."""

import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from arctis_sound_manager.core import CoreEngine


def _pulse(sinks, inputs):
    pulse = MagicMock()
    pulse.__enter__.return_value = pulse
    pulse.sink_list.return_value = sinks
    pulse.sink_input_list.return_value = inputs
    return pulse


def _install(monkeypatch, pulse):
    fake = MagicMock()
    fake.Pulse.return_value = pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake)


def test_snapshot_collects_streams_on_named_sink(monkeypatch):
    pulse = _pulse(
        [NS(index=1, name="Arctis_Game"), NS(index=2, name="Arctis_Media")],
        [NS(index=10, sink=1), NS(index=11, sink=2)],
    )
    _install(monkeypatch, pulse)
    assert CoreEngine._snapshot_sink_streams(["Arctis_Game"]) == {"Arctis_Game": [10]}


def test_restore_moves_orphaned_stream_to_new_sink(monkeypatch):
    pulse = _pulse(
        [NS(index=296, name="Arctis_Game"), NS(index=95, name="physical")],
        [NS(index=10, sink=95)],
    )
    _install(monkeypatch, pulse)
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._restore_sink_streams({"Arctis_Game": [10]}, timeout_s=0.5)
    pulse.sink_input_move.assert_called_once_with(10, 296)


def test_restore_skips_stream_already_on_sink(monkeypatch):
    pulse = _pulse([NS(index=296, name="Arctis_Game")], [NS(index=10, sink=296)])
    _install(monkeypatch, pulse)
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._restore_sink_streams({"Arctis_Game": [10]}, timeout_s=0.5)
    pulse.sink_input_move.assert_not_called()
