# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""StatusChanged: the signal the Plasma widget listens to instead of polling.

The rules pinned here are the ones a widget relies on: one signal per real
change, carrying the same payload GetStatus returns, and nothing when the
status merely stays the same.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from arctis_sound_manager.dbus_service import (ArctisManagerDbusStatusService,
                                               DbusManager)


class _FakeStatus:
    def __init__(self, manager, payloads):
        self._manager = manager
        self._payloads = list(payloads)
        self.emitted: list[str] = []

    def status_json(self) -> str:
        payload = self._payloads.pop(0)
        if not self._payloads:
            self._manager._stopping = True
        return payload

    def status_changed(self, payload: str) -> str:
        self.emitted.append(payload)
        return payload


def _run_watch(payloads) -> list[str]:
    manager = DbusManager.__new__(DbusManager)
    manager.log = MagicMock()
    manager.STATUS_WATCH_INTERVAL = 0
    fake = _FakeStatus(manager, payloads)
    asyncio.run(manager._watch_status(fake))
    return fake.emitted


def test_emits_only_on_change():
    assert _run_watch(['{}', '{}', '{"a": 1}', '{"a": 1}', '{"a": 2}']) == [
        '{"a": 1}', '{"a": 2}']


def test_first_reading_is_not_announced():
    """A listener reads GetStatus when the daemon appears; the watcher's first
    snapshot is that same state, not a change."""
    assert _run_watch(['{"a": 1}', '{"a": 1}']) == []


def test_a_failing_read_does_not_kill_the_watcher():
    manager = DbusManager.__new__(DbusManager)
    manager.log = MagicMock()
    manager.STATUS_WATCH_INTERVAL = 0
    fake = _FakeStatus(manager, ['{}', '{"a": 1}'])
    real = fake.status_json
    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')
        return real()

    fake.status_json = flaky
    asyncio.run(manager._watch_status(fake))
    assert fake.emitted == ['{"a": 1}']


def test_payload_is_what_get_status_returns():
    service = ArctisManagerDbusStatusService.__new__(ArctisManagerDbusStatusService)
    service.core_engine = MagicMock()
    service.core_engine.device_config = None
    assert json.loads(service.status_json()) == {}
    assert ArctisManagerDbusStatusService.get_status.__wrapped__(service) == service.status_json()
