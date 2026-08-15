# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which output the app's own playback should come out of.

A Qt player with no device set plays to the system default, and on a machine
running this app the system default is not where the user is listening. The
headset owns the default so its channels see every stream; someone who has
walked off with Bluetooth earbuds has told *us* about it — the Output channel
follows them — and nothing about that reaches Qt. The clip previews then play
into the headset sitting on the desk, which from the sofa is a feature that
does not work.

The choice is already made and stored: :class:`OutputMemory` keeps the order of
outputs the user has picked, and resolving it against what is plugged in right
now is exactly the question here. This module is the Qt half of that — the
ranking itself stays in output_memory, testable without a sound card.
"""

from __future__ import annotations

import logging

from arctis_sound_manager.output_memory import OutputMemory

logger = logging.getLogger(__name__)


def device_id(device) -> str:
    """A QAudioDevice's id as a plain string.

    Qt hands these out as QByteArray, and on the PipeWire/PulseAudio backend
    the bytes are the node name — the same identifier OutputMemory stores and
    `pactl` prints, which is what makes the two sides comparable at all.
    """
    try:
        return bytes(device.id()).decode(errors="replace")
    except Exception:  # noqa: BLE001 — a device we cannot name is one we cannot match
        return ""


def preferred_output_device(devices=None, default=None, memory=None):
    """The QAudioDevice the user is listening on, or None to leave Qt alone.

    None means "no opinion": no remembered device is present, so the system
    default is as good an answer as this module has, and setting it explicitly
    would only freeze a choice Qt is better placed to keep current.
    """
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except ImportError:                              # pragma: no cover - env dependent
        return None

    if devices is None:
        devices = QMediaDevices.audioOutputs()
    if default is None:
        default = QMediaDevices.defaultAudioOutput()
    if not devices:
        return None

    by_id = {device_id(d): d for d in devices}
    memory = memory if memory is not None else OutputMemory.load()
    chosen = memory.resolve(list(by_id), fallback=device_id(default) if default else None)
    if not chosen or chosen == (device_id(default) if default else None):
        return None
    logger.debug("playing to the remembered output %s", chosen)
    return by_id.get(chosen)


def apply_preferred_output(audio_output) -> None:
    """Point a QAudioOutput at that device, if there is one to point it at."""
    device = preferred_output_device()
    if device is None:
        return
    try:
        audio_output.setDevice(device)
    except Exception:  # noqa: BLE001 — a refused device must not cost the preview
        logger.debug("could not set the preview output device", exc_info=True)
