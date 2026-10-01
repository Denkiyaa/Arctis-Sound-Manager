# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

#!/usr/bin/env python3
"""Generate udev rules from device YAML files — writes to stdout.

Usage:
    python3 scripts/generate_udev_rules.py [--uaccess] [devices_dir]

--uaccess emits the 70-steelseries-arctis-uaccess.rules companion file
instead of 91-steelseries-arctis.rules (see udev_rules.py for why the ACL
tag needs its own, earlier-numbered file).

devices_dir defaults to src/arctis_sound_manager/devices/ relative to this
script. Used by the AUR PKGBUILD, RPM spec and debian/rules during package
build so rules are always generated from the source of truth (device YAMLs)
rather than hardcoded.

The actual generator lives in arctis_sound_manager.udev_rules so this script
and `asm-cli udev dump-rules` always emit identical output.
"""
import sys
from pathlib import Path

# When invoked from the package build (with the wheel not yet installed) we
# need to make src/ importable directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if (_REPO_ROOT / 'src').is_dir():
    sys.path.insert(0, str(_REPO_ROOT / 'src'))

from arctis_sound_manager.udev_rules import (  # noqa: E402
    generate_rules, generate_uaccess_rules)


def main() -> None:
    args = sys.argv[1:]
    uaccess = '--uaccess' in args
    args = [a for a in args if a != '--uaccess']
    if args:
        devices_dir = Path(args[0])
    else:
        devices_dir = _REPO_ROOT / 'src' / 'arctis_sound_manager' / 'devices'

    if not devices_dir.is_dir():
        print(f'error: devices directory not found: {devices_dir}', file=sys.stderr)
        sys.exit(1)

    generate = generate_uaccess_rules if uaccess else generate_rules
    sys.stdout.write(generate([devices_dir]))


if __name__ == '__main__':
    main()
