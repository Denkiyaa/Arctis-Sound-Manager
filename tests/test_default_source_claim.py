# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for who owns the system's default microphone.

Device init used to point the default input at effect_output.sonar-micro-eq
unconditionally. That node is only a microphone once ASM has linked one into
the micro EQ chain, so on a machine where it hasn't — the user routes the
chain themselves (``micro_input_source`` = ``"__manual__"``), or the source
they configured is unplugged — every start of ASM handed the whole desktop a
microphone that records nothing, and the user had to go re-pick their real one
in the system settings.

CoreEngine._claim_default_source now asks resolve_micro_input_source() who is
feeding the chain, and leaves the default input alone unless the answer is a
source that is actually in the graph.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arctis_sound_manager import sonar_to_pipewire as _s2p


def _engine(present=()):
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = logging.getLogger("test")
    engine.pa_audio_manager = MagicMock()
    engine.pa_audio_manager.has_source.side_effect = lambda name: name in present
    return engine


def _settings(micro_input_source):
    return SimpleNamespace(micro_input_source=micro_input_source)


# ── CoreEngine._claim_default_source ───────────────────────────────────────

def test_claims_when_the_configured_mic_is_there():
    engine = _engine(present={"alsa_input.podcast-mic"})

    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("alsa_input.podcast-mic")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_called_once_with(
        "effect_output.sonar-micro-eq")


def test_leaves_the_default_alone_in_manual_mode():
    """The setting that says "don't route my mic" also says "don't pick it"."""
    engine = _engine(present={"alsa_input.podcast-mic"})

    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__manual__")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_not_called()


def test_leaves_the_default_alone_when_the_configured_mic_is_gone():
    """A chain fed by nothing must not become everyone's default input."""
    engine = _engine(present=set())

    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("alsa_input.podcast-mic")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_not_called()


def test_leaves_the_default_alone_with_no_headset_in_auto_mode():
    engine = _engine(present=set())

    with patch.object(_s2p, "_get_physical_in", return_value=""), \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__auto__")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_not_called()


def test_claims_the_arctis_mic_in_auto_mode():
    engine = _engine(present={"alsa_input.arctis-mic"})

    with patch.object(_s2p, "_get_physical_in", return_value="alsa_input.arctis-mic"), \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__auto__")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_called_once_with(
        "effect_output.sonar-micro-eq")


def test_a_failure_to_resolve_never_breaks_device_init():
    engine = _engine()

    with patch.object(_s2p, "resolve_micro_input_source", side_effect=RuntimeError("boom")):
        engine._claim_default_source()

    engine.pa_audio_manager.set_default_source.assert_not_called()


# ── resolve_micro_input_source ─────────────────────────────────────────────

def test_resolve_returns_empty_in_manual_mode():
    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__manual__")):
        assert _s2p.resolve_micro_input_source() == ""


def test_resolve_returns_the_configured_node_name():
    with patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("alsa_input.podcast-mic")):
        assert _s2p.resolve_micro_input_source() == "alsa_input.podcast-mic"


def test_resolve_falls_back_to_the_arctis_mic():
    with patch.object(_s2p, "_get_physical_in", return_value="alsa_input.arctis-mic"), \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=SimpleNamespace()):
        assert _s2p.resolve_micro_input_source() == "alsa_input.arctis-mic"
