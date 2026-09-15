# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""USB re-enumeration helpers for issue #238 (ChatMix dead after system resume).

Pure, mockable primitives — no CoreEngine state touched here. Two escalation
paths exist because they need different privileges:

- reset_via_usbfs(): USBDEVFS_RESET via libusb's Device.reset(). Needs only an
  rw fd on the usbfs node, already granted by the MODE="0666" udev rule
  (udev_rules.py) — safe to call automatically from the unprivileged daemon.
- toggle_authorized(): writes /sys/.../authorized, which is root:root and NOT
  reachable by udev MODE=/OWNER=/GROUP= (those apply to /dev nodes, not sysfs
  attributes). Root-only, and deliberately never called from the automatic
  resume path — see cli_tools.py's `asm-cli usb reenumerate`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def sysfs_device_dir(
    bus: int | None, port_numbers: tuple[int, ...] | list[int] | None,
    sys_root: Path = Path('/sys/bus/usb/devices'),
) -> Path | None:
    """The sysfs directory for a USB device, e.g. /sys/bus/usb/devices/1-6.2.

    Same "<bus>-<port>.<port>..." convention core.py's _hid_usage_page already
    builds inline. None if bus or port_numbers is missing/empty — there is
    nothing to point at.
    """
    if not bus or not port_numbers:
        return None
    return sys_root / (f"{bus}-" + ".".join(str(p) for p in port_numbers))


def reset_via_usbfs(usb_device: Any, logger: Any) -> bool:
    """Force a USBDEVFS_RESET on *usb_device*. Returns True on success.

    On success the libusb handle is invalid: the caller MUST
    usb.util.dispose_resources(usb_device) and re-detect the device before
    using it again — reset() re-enumerates the port, which the kernel may
    assign a new address for.
    """
    import usb.core

    try:
        usb_device.reset()
        return True
    except usb.core.USBError as e:
        logger.warning("usb_reenumerate: reset failed: %r — not escalating", e)
        return False


def can_toggle_authorized(device_dir: Path) -> bool:
    """Whether this process can write device_dir/authorized (root usually required)."""
    return os.access(device_dir / 'authorized', os.W_OK)


def toggle_authorized(device_dir: Path, settle_s: float, logger: Any) -> bool:
    """Unbind/rebind a USB device by toggling its sysfs 'authorized' attribute.

    Root-only in practice (see module docstring). Not called from any
    automatic path — exposed for `asm-cli usb reenumerate` only.
    """
    authorized_path = device_dir / 'authorized'
    try:
        authorized_path.write_text('0')
        time.sleep(settle_s)
        authorized_path.write_text('1')
        return True
    except (PermissionError, OSError) as e:
        logger.warning(
            "usb_reenumerate: authorized is not writable (root required); see docs (%r)", e)
        return False
