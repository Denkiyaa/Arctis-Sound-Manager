# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ChatMix dial reporting a position it isn't in.

The dial is analogue: a headset nobody is touching still reports 100, 99, 100,
99 … and every one of those readings used to be written to the virtual sinks.
Since PULSE_MEDIA_NODE_NAME is Arctis_Game — the sink most setups have as the
system default output — the desktop answered each write with its volume OSD,
so a headset sitting on the desk flashed "99%" then "100%" over whatever the
user was doing, every few seconds.

Fixed in two places, both covered here:
  * CoreEngine._mix_is_jitter — a move of a single point on both channels is
    noise and is dropped, without moving the reference the next reading is
    compared against (so a slow, real turn still accumulates past it).
  * PulseAudioManager.set_mix — only the channel that actually moved is
    written; re-writing a sink the level it already has still makes the server
    announce a volume change.
"""

import logging
from unittest.mock import MagicMock

import pytest


# ── CoreEngine: jitter filtering ───────────────────────────────────────────

def _engine(media_mix=100, chat_mix=100):
    """A bare CoreEngine with just enough wired up for manage_mix_change."""
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = logging.getLogger("test")
    engine.media_mix = media_mix
    engine.chat_mix = chat_mix
    engine.device_config = MagicMock()
    engine.device_status = {}
    engine.pa_audio_manager = MagicMock()
    return engine


def _feed(engine, monkeypatch, media_mix, chat_mix):
    """Deliver one status frame carrying *media_mix* / *chat_mix*."""
    import arctis_sound_manager.core as core_mod

    engine.device_status = {"media_mix": media_mix, "chat_mix": chat_mix}
    monkeypatch.setattr(core_mod, "parsed_status", lambda raw, _cfg: raw)
    engine.manage_mix_change()


def test_single_point_wobble_is_not_applied(monkeypatch):
    """The reported symptom: an untouched dial must not move any volume."""
    engine = _engine()

    _feed(engine, monkeypatch, 99, 100)

    engine.pa_audio_manager.set_mix.assert_not_called()
    assert (engine.media_mix, engine.chat_mix) == (100, 100)


def test_wobble_on_both_channels_is_not_applied(monkeypatch):
    engine = _engine()

    _feed(engine, monkeypatch, 99, 99)

    engine.pa_audio_manager.set_mix.assert_not_called()


def test_real_turn_is_applied(monkeypatch):
    """A hand on the dial moves it further than the tolerance."""
    engine = _engine()

    _feed(engine, monkeypatch, 60, 100)

    engine.pa_audio_manager.set_mix.assert_called_once_with(60, 100)
    assert (engine.media_mix, engine.chat_mix) == (60, 100)


def test_slow_turn_accumulates_past_the_tolerance(monkeypatch):
    """Ignored readings must not become the new reference.

    Comparing against the last *settled* value is what makes a dial turned one
    point at a time eventually arrive, instead of drifting away for free.
    """
    engine = _engine()

    _feed(engine, monkeypatch, 99, 100)
    engine.pa_audio_manager.set_mix.assert_not_called()

    _feed(engine, monkeypatch, 98, 100)

    engine.pa_audio_manager.set_mix.assert_called_once_with(98, 100)
    assert engine.media_mix == 98


@pytest.mark.parametrize("media_mix, current", [(0, 1), (100, 99)])
def test_ends_of_travel_are_always_real(monkeypatch, media_mix, current):
    """Silence and full volume are positions the user can feel.

    Stopping a point short of either is exactly the "not quite right" the dial
    gets turned to fix, so a reading that lands on an end is taken at face
    value even when it is a single point away.
    """
    engine = _engine(media_mix=current)

    _feed(engine, monkeypatch, media_mix, 100)

    engine.pa_audio_manager.set_mix.assert_called_once_with(media_mix, 100)


def test_unchanged_reading_writes_nothing(monkeypatch):
    engine = _engine()

    _feed(engine, monkeypatch, 100, 100)

    engine.pa_audio_manager.set_mix.assert_not_called()


# ── PulseAudioManager.set_mix: no redundant writes ─────────────────────────

def _sink(node_name, pct):
    s = MagicMock()
    s.proplist = {"node.name": node_name}
    s.volume.value_flat = pct / 100
    return s


def _manager(sinks):
    from arctis_sound_manager.pactl import PulseAudioManager

    manager = PulseAudioManager.__new__(PulseAudioManager)
    manager.logger = logging.getLogger("test")
    manager.pulse = MagicMock()
    manager.get_arctis_sinks = MagicMock(return_value=sinks)
    return manager


def test_set_mix_skips_the_channel_that_did_not_move():
    game = _sink("Arctis_Game", 100)
    chat = _sink("Arctis_Chat", 100)
    manager = _manager([game, chat])

    manager.set_mix(100, 40)

    manager.pulse.volume_set_all_chans.assert_called_once_with(chat, 0.4)


def test_set_mix_writes_both_when_both_moved():
    game = _sink("Arctis_Game", 100)
    chat = _sink("Arctis_Chat", 100)
    manager = _manager([game, chat])

    manager.set_mix(70, 40)

    assert manager.pulse.volume_set_all_chans.call_count == 2


def test_set_mix_writes_nothing_when_the_sinks_already_match():
    manager = _manager([_sink("Arctis_Game", 80), _sink("Arctis_Chat", 20)])

    manager.set_mix(80, 20)

    manager.pulse.volume_set_all_chans.assert_not_called()
