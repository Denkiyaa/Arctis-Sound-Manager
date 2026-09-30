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
    written; re-writing a stream the level it already has still makes the
    server announce a volume change.

The mix itself now lands on each channel's loopback output stream
(Arctis_<Ch>_sink_out), never on the channel sink: see pactl.MIX_STREAM_NAMES.
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
    engine._mix_applied = True
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


def test_first_reading_is_written_even_at_the_default(monkeypatch):
    """The streams come back at whatever WirePlumber remembered: the first
    dial reading must land even if it matches the engine's 100/100 default,
    and even if it is only a point away from it."""
    engine = _engine()
    engine._mix_applied = False

    _feed(engine, monkeypatch, 99, 100)

    engine.pa_audio_manager.set_mix.assert_called_once_with(99, 100)
    engine.pa_audio_manager.set_mix.reset_mock()
    _feed(engine, monkeypatch, 99, 100)
    engine.pa_audio_manager.set_mix.assert_not_called()


# ── PulseAudioManager.set_mix: no redundant writes ─────────────────────────

def _stream(node_name, pct):
    s = MagicMock()
    s.proplist = {"node.name": node_name}
    s.volume.value_flat = pct / 100
    return s


def _manager(streams, channels=("game",)):
    from arctis_sound_manager.pactl import PulseAudioManager

    manager = PulseAudioManager.__new__(PulseAudioManager)
    manager.logger = logging.getLogger("test")
    manager.pulse = MagicMock()
    manager.pulse.sink_input_list.return_value = streams
    manager.sink_list_wrapper = MagicMock(return_value=[])
    manager._chatmix_channels = MagicMock(return_value=list(channels))
    return manager


def _touched(manager):
    return [call.args[0] for call in manager.pulse.volume_set_all_chans.call_args_list]


def test_set_mix_skips_the_channel_that_did_not_move():
    game = _stream("Arctis_Game_sink_out", 100)
    chat = _stream("Arctis_Chat_sink_out", 100)
    manager = _manager([game, chat])

    manager.set_mix(100, 40)

    manager.pulse.volume_set_all_chans.assert_called_once_with(chat, 0.4)


def test_set_mix_writes_both_when_both_moved():
    manager = _manager([_stream("Arctis_Game_sink_out", 100), _stream("Arctis_Chat_sink_out", 100)])

    manager.set_mix(70, 40)

    assert manager.pulse.volume_set_all_chans.call_count == 2


def test_set_mix_writes_nothing_when_the_streams_already_match():
    manager = _manager([_stream("Arctis_Game_sink_out", 80), _stream("Arctis_Chat_sink_out", 20)])

    manager.set_mix(80, 20)

    manager.pulse.volume_set_all_chans.assert_not_called()


def test_set_mix_never_touches_the_channel_sinks():
    """The sinks carry the user's own channel levels, and one of them is the
    desktop default output: writing it is what popped Plasma's volume OSD."""
    game_sink = _stream("Arctis_Game", 100)
    media_sink = _stream("Arctis_Media", 100)
    manager = _manager([game_sink, media_sink, _stream("Arctis_Game_sink_out", 100)],
                       channels=("game", "media"))
    manager.sink_list_wrapper.return_value = [game_sink, media_sink]

    manager.set_mix(30, 40)

    assert game_sink not in _touched(manager)
    assert media_sink not in _touched(manager)


# ── PulseAudioManager.set_mix: ChatMix channels (#249/#269) ────────────────

def test_set_mix_moves_only_the_configured_channels():
    game = _stream("Arctis_Game_sink_out", 100)
    media = _stream("Arctis_Media_sink_out", 100)
    manager = _manager([game, _stream("Arctis_Chat_sink_out", 100), media], channels=["media"])

    manager.set_mix(70, 40)

    manager.pulse.volume_set_all_chans.assert_any_call(media, 0.7)
    assert game not in _touched(manager)


def test_set_mix_moves_aux_alongside_game_when_configured():
    game = _stream("Arctis_Game_sink_out", 100)
    aux = _stream("Arctis_Aux_sink_out", 100)
    manager = _manager([game, _stream("Arctis_Chat_sink_out", 100), aux], channels=["game", "aux"])

    manager.set_mix(55, 40)

    manager.pulse.volume_set_all_chans.assert_any_call(aux, 0.55)
    manager.pulse.volume_set_all_chans.assert_any_call(game, 0.55)


def test_set_mix_releases_a_channel_taken_off_the_dial():
    """Unticking Media must not leave it stuck at the level the dial last
    gave it: a channel off the dial plays at full mix level."""
    media = _stream("Arctis_Media_sink_out", 30)
    manager = _manager([_stream("Arctis_Game_sink_out", 70), media], channels=["game"])

    manager.set_mix(70, 100)

    manager.pulse.volume_set_all_chans.assert_called_once_with(media, 1.0)


def test_set_mix_skips_configured_channel_already_at_target():
    media = _stream("Arctis_Media_sink_out", 70)
    manager = _manager([_stream("Arctis_Chat_sink_out", 100), media], channels=["media"])

    manager.set_mix(70, 40)

    assert media not in _touched(manager)


def test_mix_stream_levels_reads_each_channel():
    from arctis_sound_manager.pactl import mix_stream_levels

    pulse = MagicMock()
    pulse.sink_input_list.return_value = [
        _stream("Arctis_Game_sink_out", 60), _stream("Arctis_Chat_sink_out", 100),
        _stream("Arctis_Game", 20),  # the sink, not the stream: ignored
    ]

    assert mix_stream_levels(pulse) == {"game": 60, "chat": 100, "media": None, "aux": None}
