# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_device_override_reconcile.py — profile fixes must reach existing users.

`asm-setup` copied every packaged device profile into
~/.config/arctis_manager/devices/, a folder that takes precedence over the
packaged one and that no upgrade ever refreshes. Since asm-setup only runs at
first GUI launch, every profile fix shipped afterwards was inert for that user:
new product ids, status offsets, EQ opcodes. Silently, and for good.

The three rules under test, ordered by how much of the user's own work they
risk: delete what provably has none, move aside what cannot be told apart from
a deliberate edit, and never touch a profile ASM does not ship.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arctis_sound_manager.device_override_reconcile import (
    DISABLED_DIRNAME, reconcile_home_device_overrides)

PACKAGED = "device:\n  name: Packaged\n  vendor_id: 0x1038\n"
EDITED = "device:\n  name: Packaged\n  vendor_id: 0x1038\n  product_ids: [0x9999]\n"


@pytest.fixture
def dirs(tmp_path) -> tuple[Path, Path]:
    home = tmp_path / "config" / "arctis_manager" / "devices"
    packaged = tmp_path / "pkg" / "devices"
    home.mkdir(parents=True)
    packaged.mkdir(parents=True)
    return home, packaged


# ── Rule 1: an identical copy is pure shadow ────────────────────────────────

def test_a_copy_identical_to_the_packaged_profile_is_removed(dirs):
    home, packaged = dirs
    (packaged / "nova_7.yaml").write_text(PACKAGED)
    (home / "nova_7.yaml").write_text(PACKAGED)

    report = reconcile_home_device_overrides(home, packaged)

    assert report.removed == ["nova_7.yaml"]
    assert not (home / "nova_7.yaml").exists()


def test_removing_the_copy_lets_a_shipped_fix_through(dirs):
    """The whole point: after the pass, the packaged profile is what loads."""
    home, packaged = dirs
    (home / "nova_7.yaml").write_text(PACKAGED)          # copied at first run
    (packaged / "nova_7.yaml").write_text(PACKAGED)

    reconcile_home_device_overrides(home, packaged)
    # An upgrade now ships a new product id for that family…
    (packaged / "nova_7.yaml").write_text(
        PACKAGED + "  product_ids: [0x2298]\n")

    assert list(home.glob("*.yaml")) == [], "nothing left to shadow it"


# ── Rule 2: a differing copy is parked, never destroyed ─────────────────────

def test_a_differing_copy_is_moved_aside_and_no_longer_loaded(dirs):
    home, packaged = dirs
    (packaged / "nova_7.yaml").write_text(PACKAGED)
    (home / "nova_7.yaml").write_text(EDITED)

    report = reconcile_home_device_overrides(home, packaged)

    assert report.disabled == ["nova_7.yaml"]
    assert not (home / "nova_7.yaml").exists(), "must stop shadowing"
    moved = home.parent / DISABLED_DIRNAME / "nova_7.yaml"
    assert moved.is_file(), "and must not be destroyed"
    assert moved.read_text() == EDITED, "the user's edit is preserved verbatim"


def test_a_stale_copy_of_an_older_release_is_also_parked(dirs):
    """Indistinguishable from a deliberate edit — and the harmful case.

    This is what an upgraded install actually looks like: last release's file,
    differing from the new packaged one, quietly winning.
    """
    home, packaged = dirs
    (home / "wired.yaml").write_text("device:\n  init: [0x06, 0x49, 0x00]\n")
    (packaged / "wired.yaml").write_text("device:\n  init: [0x06, 0x49, 0x01]\n")

    report = reconcile_home_device_overrides(home, packaged)

    assert report.disabled == ["wired.yaml"]


# ── Rule 3: what this folder is genuinely for ───────────────────────────────

def test_a_profile_asm_does_not_ship_is_left_strictly_alone(dirs):
    home, packaged = dirs
    (packaged / "nova_7.yaml").write_text(PACKAGED)
    (home / "my_unsupported_headset.yaml").write_text(EDITED)

    report = reconcile_home_device_overrides(home, packaged)

    assert report.kept == ["my_unsupported_headset.yaml"]
    assert (home / "my_unsupported_headset.yaml").read_text() == EDITED
    assert not (home.parent / DISABLED_DIRNAME).exists()


# ── Properties the daemon start path depends on ─────────────────────────────

def test_the_pass_is_idempotent(dirs):
    home, packaged = dirs
    (packaged / "a.yaml").write_text(PACKAGED)
    (packaged / "b.yaml").write_text(PACKAGED)
    (home / "a.yaml").write_text(PACKAGED)
    (home / "b.yaml").write_text(EDITED)

    first = reconcile_home_device_overrides(home, packaged)
    second = reconcile_home_device_overrides(home, packaged)

    assert first.changed is True
    assert second.changed is False, "runs on every daemon start — must settle"
    assert second.removed == [] and second.disabled == []


def test_dry_run_reports_without_touching_anything(dirs):
    home, packaged = dirs
    (packaged / "a.yaml").write_text(PACKAGED)
    (packaged / "b.yaml").write_text(PACKAGED)
    (home / "a.yaml").write_text(PACKAGED)
    (home / "b.yaml").write_text(EDITED)

    report = reconcile_home_device_overrides(home, packaged, dry_run=True)

    assert report.removed == ["a.yaml"] and report.disabled == ["b.yaml"]
    assert (home / "a.yaml").exists() and (home / "b.yaml").exists()


def test_a_missing_override_folder_is_not_an_error(tmp_path):
    packaged = tmp_path / "pkg"
    packaged.mkdir()
    report = reconcile_home_device_overrides(tmp_path / "nope", packaged)
    assert report.changed is False


def test_an_unreadable_folder_never_stops_the_daemon(dirs, monkeypatch):
    """Housekeeping must not be able to keep ASM from starting."""
    home, packaged = dirs
    (packaged / "a.yaml").write_text(PACKAGED)
    (home / "a.yaml").write_text(PACKAGED)

    def boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", boom)

    report = reconcile_home_device_overrides(home, packaged)

    assert report.kept == ["a.yaml"], "falls back to the status quo, no crash"
    assert (home / "a.yaml").exists()
