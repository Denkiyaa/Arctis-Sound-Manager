# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_udev_rules.py — udev rules generation from device YAMLs.

Covers the regressions behind #146:
  1. A user override in ~/.config/arctis_manager/devices wins over the bundled
     YAML of the same family, so a hand-added PID reaches the rules file.
  2. Every PID declared in the bundled YAMLs ends up in the rendered rules.
  3. Running elevated (sudo / pkexec) still reads the invoking user's override
     folder instead of /root's.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from arctis_sound_manager.udev_rules import generate_rules, load_devices

SRC_DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

_OVERRIDE_YAML = """\
device:
  name: SteelSeries Arctis Nova 7P (Gen 2)
  vendor_id: 0x1038
  product_ids:
    - 0x22a7
    - 0xdead
"""


def _write_override(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "nova_7p_perc_battery.yaml"
    path.write_text(_OVERRIDE_YAML)
    return path


def test_home_override_wins_over_bundled_yaml(tmp_path: Path):
    """The HOME copy of a family must drive the rules, not the packaged one."""
    home = _write_override(tmp_path / "devices").parent

    rules = generate_rules([home, SRC_DEVICES])

    assert 'ATTRS{idProduct}=="dead"' in rules
    # The family must appear exactly once, from the override.
    assert rules.count("# SteelSeries Arctis Nova 7P (Gen 2)") == 1


def test_bundled_pids_all_rendered():
    """Every PID declared in the shipped YAMLs makes it into the rules file."""
    devices = load_devices([SRC_DEVICES])
    rules = generate_rules([SRC_DEVICES])

    for _vid, pids, name in devices:
        for pid in pids:
            assert f'ATTRS{{idProduct}}=="{pid:04x}"' in rules, f"{name}: 0x{pid:04x} missing"


def test_nova_7p_gen2_pids_present():
    """#146: 0x2298 (Arctis Nova 7P Gen 2) is declared alongside 0x22a7."""
    rules = generate_rules([SRC_DEVICES])

    assert 'ATTRS{idProduct}=="2298"' in rules
    assert 'ATTRS{idProduct}=="22a7"' in rules


def test_elevated_run_reads_invoking_user_devices(tmp_path: Path, monkeypatch):
    """Under sudo, DEVICES_CONFIG_FOLDER must include the real user's folder."""
    import pwd

    fake_home = tmp_path / "home" / "alberto"
    _write_override(fake_home / ".config" / "arctis_manager" / "devices")

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.delenv("PKEXEC_UID", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/root")))
    monkeypatch.setattr(
        pwd, "getpwuid", lambda uid: pwd.struct_passwd(
            ("alberto", "x", 1000, 1000, "", str(fake_home), "/bin/sh")
        )
    )

    import arctis_sound_manager.constants as constants
    constants = importlib.reload(constants)
    try:
        expected = fake_home / ".config" / "arctis_manager" / "devices"
        assert constants.INVOKING_USER_CONFIG_FOLDER == expected
        assert constants.DEVICES_CONFIG_FOLDER[0] == expected

        rules = generate_rules(constants.DEVICES_CONFIG_FOLDER)
        assert 'ATTRS{idProduct}=="dead"' in rules
    finally:
        # Leave the module in its unelevated state for the rest of the suite.
        monkeypatch.undo()
        importlib.reload(constants)


def test_unelevated_run_has_no_invoking_user_folder(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_UID", "1000")

    import arctis_sound_manager.constants as constants
    constants = importlib.reload(constants)
    try:
        assert constants.INVOKING_USER_CONFIG_FOLDER is None
        assert constants.DEVICES_CONFIG_FOLDER[0] == constants.HOME_CONFIG_FOLDER
    finally:
        monkeypatch.undo()
        importlib.reload(constants)
