# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for QSystrayApp._extract_battery_percent — the tray battery helper.

The wireless adapter keeps reporting a battery percentage even after the
headset is switched off, so a percentage alone is not a reliable "present"
signal. The helper must return None when headset_power_status says 'off'
so the tray battery item is hidden (#124).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.systray_app import QSystrayApp

_extract = QSystrayApp._extract_battery_percent


def _status(power: str | None, pct: int = 80) -> dict:
    headset: dict = {
        "headset_battery_charge": {"value": pct, "type": "percentage"},
    }
    if power is not None:
        headset["headset_power_status"] = {"value": power, "type": "label"}
    return {"headset": headset}


def test_returns_percent_when_powered_on():
    assert _extract(_status("on", 73)) == 73


def test_returns_none_when_powered_off():
    assert _extract(_status("off", 73)) is None


def test_returns_none_when_powered_offline():
    # 'offline' is the vocabulary used by Nova Pro Wireless/Elite/Omni and
    # Arctis Pro Wireless — the original #124 fix only handled 'off'.
    assert _extract(_status("offline", 73)) is None


def test_returns_percent_when_power_status_online():
    assert _extract(_status("online", 60)) == 60


def test_returns_percent_when_cable_charging():
    # On the charging stand, the Nova Pro Wireless is not "off".
    assert _extract(_status("cable_charging", 100)) == 100


def test_returns_percent_when_power_status_absent():
    # No headset_power_status key: keep the pre-#124 behaviour (show battery).
    assert _extract(_status(None, 42)) == 42


def test_finds_battery_in_non_headset_category():
    # Power status lives in the same category as the battery, whatever its name.
    status = {
        "power": {
            "headset_battery_charge": {"value": 55, "type": "percentage"},
            "headset_power_status": {"value": "off", "type": "label"},
        }
    }
    assert _extract(status) is None


def test_ignores_non_percentage_battery_entry():
    status = {"headset": {"headset_battery_charge": {"value": 3, "type": "discrete"}}}
    assert _extract(status) is None


def test_handles_empty_and_malformed_input():
    assert _extract({}) is None
    assert _extract("not a dict") is None
    assert _extract({"headset": None}) is None


# ── when the menu is rebuilt ──────────────────────────────────────────────────

def _menu_stub(visible: bool = False):
    import types

    rebuilds: list[int] = []
    stub = types.SimpleNamespace(
        menu=types.SimpleNamespace(isVisible=lambda: visible),
        menu_setup=lambda: rebuilds.append(1),
        _menu_open=False,
        _menu_stale=False,
        do_polling=False,
        last_device_status={},
        _update_tray_icon=lambda _status: None,
    )
    # on_new_status goes through this, so the stub needs it bound to itself.
    from arctis_sound_manager.gui.systray_app import QSystrayApp

    stub._rebuild_menu_if_stale = lambda: QSystrayApp._rebuild_menu_if_stale(stub)
    return stub, rebuilds


def test_opening_the_menu_does_not_rebuild_it():
    """aboutToShow is when KDE exports the menu over DBusMenu. Clearing it
    there leaves the exporter with actions it never gave ids to — the journal
    fills with "fillLayoutItem: No id for action" and the entries Plasma draws
    map to nothing, so clicking them does nothing."""
    from arctis_sound_manager.gui.systray_app import QSystrayApp

    stub, rebuilds = _menu_stub()

    QSystrayApp.start_polling(stub)

    assert rebuilds == []
    assert stub._menu_open and stub.do_polling


def test_a_status_arriving_with_the_menu_closed_is_drawn_at_once():
    from arctis_sound_manager.gui.systray_app import QSystrayApp

    stub, rebuilds = _menu_stub()

    QSystrayApp.on_new_status(stub, {"dev": {}})

    assert rebuilds == [1]
    assert stub._menu_stale is False


def test_a_status_arriving_with_the_menu_open_is_held_not_dropped():
    """Dropping it left the menu showing the previous status until some later
    poll happened to differ — for a headset that just connected, never."""
    from arctis_sound_manager.gui.systray_app import QSystrayApp

    stub, rebuilds = _menu_stub()
    QSystrayApp.start_polling(stub)          # menu open

    QSystrayApp.on_new_status(stub, {"dev": {}})

    assert rebuilds == []
    assert stub._menu_stale is True

    stub._menu_open = False
    QSystrayApp._rebuild_menu_if_stale(stub)

    assert rebuilds == [1]


def test_a_menu_qt_is_showing_itself_is_left_alone():
    """The plain-Qt popup has a real surface; clearing it under queued paint
    events is a use-after-free in QWaylandWindow."""
    from arctis_sound_manager.gui.systray_app import QSystrayApp

    stub, rebuilds = _menu_stub(visible=True)
    stub._menu_stale = True

    QSystrayApp._rebuild_menu_if_stale(stub)

    assert rebuilds == []
