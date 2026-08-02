#!/bin/sh
# Restart ASM's user services after a package upgrade.
#
# Why this exists: a package manager replaces files on disk, it does not touch
# running processes. Python has already loaded the old modules into memory, so
# asm-daemon keeps executing the previous version until something restarts it —
# for most people, until their next reboot. Someone who upgrades *for* a fix
# then finds the bug still there, and reports it against a version they are not
# running.
#
# Called from the post-upgrade scriptlet of every packaging (pacman .install,
# RPM %post, deb postinst), which run as root while user sessions are live.
#
# Deliberately try-restart, not restart: it only touches services that are
# already running. Someone who stopped ASM on purpose does not get it started
# again by installing an update.
#
# Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
set -u

SERVICES="arctis-manager.service arctis-video-router.service arctis-stream-guard.service arctis-gui.service"

restart_for_session() {
    uid="$1"
    user="$2"

    # No session bus means no running user manager to talk to — nothing of ours
    # can be running there either.
    [ -S "/run/user/${uid}/bus" ] || return 0

    # daemon-reload first: the upgrade may have changed the unit files
    # themselves, and try-restart would otherwise re-launch the old definition.
    runuser -u "$user" -- env \
        XDG_RUNTIME_DIR="/run/user/${uid}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
        systemctl --user daemon-reload >/dev/null 2>&1 || true

    # shellcheck disable=SC2086  # SERVICES is a deliberate word list
    runuser -u "$user" -- env \
        XDG_RUNTIME_DIR="/run/user/${uid}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
        systemctl --user try-restart $SERVICES >/dev/null 2>&1 || true
}

command -v loginctl >/dev/null 2>&1 || exit 0
command -v runuser  >/dev/null 2>&1 || exit 0

# "loginctl list-users" prints: UID USER [LINGER] [STATE]
loginctl list-users --no-legend 2>/dev/null | while read -r uid user _rest; do
    case "$uid" in
        ''|*[!0-9]*) continue ;;   # header or malformed line
    esac
    restart_for_session "$uid" "$user"
done

# The GUI is not always a systemd service — it is commonly started by the
# desktop's autostart, and killing someone's window from a package transaction
# would be rude. It notices the upgrade on its own and offers to restart
# (see runtime_staleness.py).
exit 0
