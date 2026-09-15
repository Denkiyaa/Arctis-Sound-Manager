# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for usb_reenumerate.py — the USB re-enumeration primitives behind
issue #238 (ChatMix dead on the Nova Pro Omni after a system resume, until the
DAC is physically replugged).

Pure/mockable: no real USB or root access needed.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager.usb_reenumerate import (can_toggle_authorized,
                                                   reset_via_usbfs,
                                                   sysfs_device_dir,
                                                   toggle_authorized)


# ── sysfs_device_dir ─────────────────────────────────────────────────────────


def test_sysfs_device_dir_simple_port(tmp_path):
    result = sysfs_device_dir(1, (6,), sys_root=tmp_path)
    assert result == tmp_path / "1-6"


def test_sysfs_device_dir_multi_level_port(tmp_path):
    result = sysfs_device_dir(1, (6, 2), sys_root=tmp_path)
    assert result == tmp_path / "1-6.2"


def test_sysfs_device_dir_none_when_bus_missing(tmp_path):
    assert sysfs_device_dir(None, (6,), sys_root=tmp_path) is None


def test_sysfs_device_dir_none_when_ports_missing(tmp_path):
    assert sysfs_device_dir(1, None, sys_root=tmp_path) is None
    assert sysfs_device_dir(1, (), sys_root=tmp_path) is None


# ── reset_via_usbfs ──────────────────────────────────────────────────────────


def test_reset_via_usbfs_success():
    device = MagicMock()
    logger = MagicMock()

    assert reset_via_usbfs(device, logger) is True
    device.reset.assert_called_once()
    logger.warning.assert_not_called()


def test_reset_via_usbfs_logs_and_returns_false_on_usberror():
    import usb.core

    device = MagicMock()
    device.reset.side_effect = usb.core.USBError("no such device")
    logger = MagicMock()

    assert reset_via_usbfs(device, logger) is False
    logger.warning.assert_called_once()


# ── can_toggle_authorized ────────────────────────────────────────────────────


def test_can_toggle_authorized_true_when_writable(tmp_path):
    device_dir = tmp_path / "1-6"
    device_dir.mkdir()
    (device_dir / "authorized").write_text("1")

    assert can_toggle_authorized(device_dir) is True


def test_can_toggle_authorized_false_when_read_only(tmp_path):
    device_dir = tmp_path / "1-6"
    device_dir.mkdir()
    authorized = device_dir / "authorized"
    authorized.write_text("1")
    authorized.chmod(0o444)

    try:
        with patch("os.access", return_value=False):
            assert can_toggle_authorized(device_dir) is False
    finally:
        authorized.chmod(0o644)


def test_can_toggle_authorized_false_when_missing(tmp_path):
    device_dir = tmp_path / "1-6"
    device_dir.mkdir()
    assert can_toggle_authorized(device_dir) is False


# ── toggle_authorized ────────────────────────────────────────────────────────


def test_toggle_authorized_writes_0_then_1(tmp_path, monkeypatch):
    device_dir = tmp_path / "1-6"
    device_dir.mkdir()
    authorized = device_dir / "authorized"
    authorized.write_text("1")

    writes: list[str] = []
    real_write_text = type(authorized).write_text

    def fake_write_text(self, data, *a, **kw):
        writes.append(data)
        return real_write_text(self, data, *a, **kw)

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("pathlib.Path.write_text", fake_write_text)

    logger = MagicMock()
    assert toggle_authorized(device_dir, settle_s=0.5, logger=logger) is True
    assert writes == ["0", "1"]
    assert sleeps == [0.5]


def test_toggle_authorized_returns_false_on_permission_error(tmp_path, monkeypatch):
    device_dir = tmp_path / "1-6"
    device_dir.mkdir()
    (device_dir / "authorized").write_text("1")

    def raise_permission_error(self, data, *a, **kw):
        raise PermissionError("root required")

    monkeypatch.setattr("pathlib.Path.write_text", raise_permission_error)

    logger = MagicMock()
    assert toggle_authorized(device_dir, settle_s=0.01, logger=logger) is False
    logger.warning.assert_called_once()
