# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import logging
from typing import Any

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import BusType
from dbus_next.errors import (DBusError, InvalidBusNameError,
                              InvalidObjectPathError)

from arctis_sound_manager.core import CoreEngine


class DbusAwake:
    _instance = None

    # Delays (seconds) after resume before re-asserting audio routing. Two passes:
    # the first once the PipeWire graph has settled, the second to catch a late
    # WirePlumber re-link of stream targets on a slow resume (issue #128).
    _WAKE_SETTLE_S = 3.0
    _WAKE_RECHECK_S = 5.0

    @staticmethod
    def get_instance() -> 'DbusAwake':
        if DbusAwake._instance is None:
            DbusAwake._instance = DbusAwake()

        return DbusAwake._instance

    def __init__(self):
        self.log = logging.getLogger('DbusAwake')

    async def start(self, core_engine: CoreEngine) -> asyncio.Future[Any]:
        self.log.info("Initializing service...")

        self.core_engine = core_engine

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        
        bus_name = 'org.freedesktop.login1'
        object_path = '/org/freedesktop/login1'

        try:
            introspection = await bus.introspect(bus_name, object_path)
            obj = bus.get_proxy_object(bus_name, object_path, introspection)
            manager = obj.get_interface('org.freedesktop.login1.Manager')

            manager.on_prepare_for_sleep(self.on_prepare_for_sleep)

            return asyncio.get_event_loop().create_future()
        except InvalidObjectPathError:
            self.log.error('Failed to introspect org.freedesktop.login1 : /org/freedesktop/login1. Object path is invalid.')
            return
        except InvalidBusNameError:
            self.log.error('Failed to connect to org.freedesktop.login1 : /org/freedesktop/login1. Bus name is invalid.')
            return
        except DBusError as e:
            self.log.error(f'Failed to connect to org.freedesktop.login1 : /org/freedesktop/login1. DBus error: {e}')
            return
        except Exception as e:
            self.log.error(f'Failed to connect to org.freedesktop.login1 : /org/freedesktop/login1. Unexpected error: {e}')
            return

    def on_prepare_for_sleep(self, going_to_sleep: bool) -> None:
        if going_to_sleep:
            try:
                self.core_engine.prepare_for_sleep()
            except Exception as e:
                self.log.warning(f"prepare_for_sleep failed: {e}")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (should not happen inside the daemon) — best
            # effort: re-acquire synchronously, skip the async probe/escalation
            # (issue #238) since those need the loop's listen-endpoint task.
            try:
                self.core_engine.configure_virtual_sinks()
            except Exception as e:
                self.log.warning(f"resume_from_sleep (no loop) failed: {e}")
            try:
                self.core_engine.reconcile_audio_routing_for_power_state()
            except Exception as e:
                self.log.warning(f"Post-wake routing reconciliation failed: {e}")
            return

        # Re-acquire the device (issue #238 — replaces the old bare
        # init_device() call, which wrote over the same libusb handle that
        # just survived suspend instead of re-claiming it).
        loop.create_task(self._resume_from_sleep_task())

        # Reconcile audio routing a moment later, independently of the resume
        # task above: on resume PipeWire/WirePlumber re-links streams to their
        # remembered targets as the graph settles, pulling media apps back
        # onto Arctis_Media even with the headset off (issue #128). Scheduled
        # unconditionally so a slow or failed USB reset never delays this
        # guard.
        loop.create_task(self._reconcile_routing_after_wake())

    async def _resume_from_sleep_task(self) -> None:
        try:
            await self.core_engine.resume_from_sleep()
        except Exception as e:
            self.log.warning(f"resume_from_sleep failed: {e}")

    async def _reconcile_routing_after_wake(self) -> None:
        for delay in (self._WAKE_SETTLE_S, self._WAKE_RECHECK_S):
            await asyncio.sleep(delay)
            try:
                self.core_engine.reconcile_audio_routing_for_power_state()
            except Exception as e:
                self.log.warning(f"Post-wake routing reconciliation failed: {e}")
