# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""#181 — in a rootless container the pyudev monitor sets up without error and
then never delivers an event: libudev drops every netlink message whose sender
uid is not 0, and the host's root is unmapped there. Reproduced in a SteamOS VM
under Distrobox: a raw NETLINK_KOBJECT_UEVENT socket saw the headset's
remove/add, pyudev (udev and kernel sources alike) saw nothing, and a headset
plugged in after ASM started was never picked up.

The watchdog (test_usb_devices_monitor_watchdog.py) cannot catch this — the
observer thread stays alive — so the backend has to be chosen up front.
"""

import pytest

from arctis_sound_manager import usb_devices_monitor as mod


@pytest.mark.parametrize("uid_map, unmapped", [
    ("         0          0 4294967295\n", False),          # host
    ("         0       1000          1\n"
     "         1     100000      65536\n", True),           # rootless podman
    ("         0          0      65536\n", False),          # rootful container
    ("", True),                                             # nothing mapped
])
def test_host_root_unmapped(tmp_path, uid_map, unmapped):
    path = tmp_path / "uid_map"
    path.write_text(uid_map)
    assert mod._host_root_unmapped(str(path)) is unmapped


def test_unreadable_uid_map_keeps_pyudev(tmp_path):
    assert mod._host_root_unmapped(str(tmp_path / "missing")) is False


def test_rootless_container_goes_straight_to_polling(monkeypatch):
    monkeypatch.setattr(mod, "_PYUDEV_AVAILABLE", True)
    monkeypatch.setattr(mod, "_host_root_unmapped", lambda: True)
    monkeypatch.setattr(mod, "pyudev", None)  # must not even be touched
    monitor = mod.USBDevicesMonitor()
    assert monitor._backend == "polling"
    assert monitor.monitor is None
