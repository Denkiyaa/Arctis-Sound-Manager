# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path

_XDG_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config'))


def _invoking_user_config_home() -> Path | None:
    """XDG config home of the user who invoked us, when running as root via sudo/pkexec.

    `sudo asm-cli udev write-rules` (what the docs and the CLI hints tell users
    to run) resolves Path.home() to /root, so device YAML overrides living in
    the real user's ~/.config/arctis_manager/devices were invisible and never
    made it into the generated rules (#146). Returns None when not elevated, or
    when the invoking user cannot be resolved. Read-only use only: never write
    into the returned folder as root, that would leave root-owned files behind.
    """
    if os.geteuid() != 0:
        return None

    uid = os.environ.get('PKEXEC_UID') or os.environ.get('SUDO_UID')
    if uid is None:
        return None

    try:
        import pwd

        home = Path(pwd.getpwuid(int(uid)).pw_dir)
    except (KeyError, ValueError, OSError):
        return None

    if home == Path.home():
        return None

    return home / '.config'

# /DBus
DBUS_BUS_NAME = 'name.giacomofurlan.ArctisManager.Next'
DBUS_OBJECT_BASE_PATH = '/name/giacomofurlan/ArctisManager/Next'

DBUS_SETTINGS_INTERFACE_NAME = f'{DBUS_BUS_NAME}.Settings'
DBUS_SETTINGS_OBJECT_PATH = f'{DBUS_OBJECT_BASE_PATH}/Settings'

DBUS_STATUS_INTERFACE_NAME = f'{DBUS_BUS_NAME}.Status'
DBUS_STATUS_OBJECT_PATH = f'{DBUS_OBJECT_BASE_PATH}/Status'

DBUS_CONFIG_INTERFACE_NAME = f'{DBUS_BUS_NAME}.Config'
DBUS_CONFIG_OBJECT_PATH = f'{DBUS_OBJECT_BASE_PATH}/Config'
# ./DBus

# Systemd
SYSTEMD_SERVICE_NAME = 'arctis-manager.service'
HOME_SYSTEMD_SERVICE_FOLDER = _XDG_CONFIG / 'systemd' / 'user'
HOME_DINIT_SERVICE_FOLDER = _XDG_CONFIG / 'dinit.d'
# ./Systemd

PULSE_MEDIA_NODE_NAME = 'Arctis_Game'
PULSE_CHAT_NODE_NAME = 'Arctis_Chat'

STEELSERIES_VENDOR_ID = 0x1038

SETTINGS_FOLDER = _XDG_CONFIG / 'arctis_manager' / 'settings'

HOME_LANG_FOLDER = _XDG_CONFIG / 'arctis_manager' / 'lang'

HOME_CONFIG_FOLDER = _XDG_CONFIG / 'arctis_manager' / 'devices'
SRC_CONFIG_FOLDER = Path(__file__).parent / 'devices'

# Only set when running elevated (sudo / pkexec): the device folder of the user
# who invoked us, read so their YAML overrides still count. See
# _invoking_user_config_home().
_INVOKING_USER_XDG_CONFIG = _invoking_user_config_home()
INVOKING_USER_CONFIG_FOLDER: Path | None = (
    _INVOKING_USER_XDG_CONFIG / 'arctis_manager' / 'devices'
    if _INVOKING_USER_XDG_CONFIG is not None else None
)

DEVICES_CONFIG_FOLDER: list[Path] = [HOME_CONFIG_FOLDER, SRC_CONFIG_FOLDER]
if INVOKING_USER_CONFIG_FOLDER is not None:
    DEVICES_CONFIG_FOLDER.insert(0, INVOKING_USER_CONFIG_FOLDER)

UDEV_RULES_PATHS = [
    # /etc has highest priority — local admin / asm-cli writes here.
    '/etc/udev/rules.d/91-steelseries-arctis.rules',
    # Distro-installed rules (Arch, Fedora, modern Debian/Ubuntu with usrmerge).
    '/usr/lib/udev/rules.d/91-steelseries-arctis.rules',
    # Pre-usrmerge Debian / Ubuntu locations, kept for backwards compat.
    '/lib/udev/rules.d/91-steelseries-arctis.rules',
    # Runtime overlay (transient, used by some package managers and NixOS).
    '/run/udev/rules.d/91-steelseries-arctis.rules',
]
