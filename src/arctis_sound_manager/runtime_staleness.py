# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Detect that the package was upgraded underneath a running process.

A package manager replaces files on disk; it does not touch processes. Python
loaded its modules at startup and keeps executing them, so after `pacman -Syu`,
`dnf upgrade` or `apt upgrade`, the GUI and the daemon go on running the version
they started with — usually until the next reboot.

That is worse than it sounds. Someone upgrades *because* of a fix, sees the bug
still there, and reports it against a version number they are not running. The
bug report says 1.2.14; the code answering the report is 1.2.12.

Package scriptlets restart the user services (see
``scripts/restart-user-services.sh``), but the GUI is commonly started by the
desktop's autostart rather than systemd, and killing someone's open window from
inside a package transaction would be rude. So the GUI checks for itself and
offers.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import subprocess
import sys

from arctis_sound_manager.utils import project_version

log = logging.getLogger(__name__)

#: Version of the code this process is actually executing. Captured at import,
#: which for the GUI and the daemon is startup — before any upgrade can land.
RUNNING_VERSION: str = project_version()

_UNKNOWN = ("", "dev")

_USER_SERVICES = (
    "arctis-manager.service",
    "arctis-video-router.service",
)


def installed_version() -> str:
    """Version currently on disk, re-read rather than remembered."""
    # importlib.metadata caches its sys.path scan; an upgrade that swaps the
    # dist-info directory would otherwise stay invisible for the life of the
    # process.
    importlib.invalidate_caches()
    return project_version()


def upgraded_under_us() -> str | None:
    """The newly installed version if the package changed under us, else None.

    Any comparison involving an unknown version ("dev", a source checkout, an
    editable install) returns None: there is nothing meaningful to compare, and
    a spurious "please restart" banner is worse than no banner.
    """
    if RUNNING_VERSION in _UNKNOWN:
        return None
    on_disk = installed_version()
    if on_disk in _UNKNOWN or on_disk == RUNNING_VERSION:
        return None
    return on_disk


def restart_user_services() -> None:
    """Restart ASM's own systemd user services, if they are running.

    try-restart, not restart: a service someone deliberately stopped stays
    stopped. Needs no privileges — these are the user's own units.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    try:
        subprocess.run([systemctl, "--user", "try-restart", *_USER_SERVICES],
                       capture_output=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("Could not restart user services: %s", exc)


def restart_gui() -> None:
    """Replace this process with a fresh one, running the code now on disk.

    execv rather than spawn-and-exit: same pid, no window flash from an
    orphaned parent, and no chance of two GUIs briefly fighting over the tray
    icon and the D-Bus name.
    """
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except OSError as exc:
        # Never leave the caller believing the restart happened.
        log.error("Could not restart the GUI in place: %s", exc)
        raise
