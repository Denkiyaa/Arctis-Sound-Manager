# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for runtime_staleness — detecting a package upgraded under a live process."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager import runtime_staleness as rs


def test_an_upgrade_under_a_running_process_is_reported(monkeypatch):
    """The whole point: files changed on disk, this process still runs the old code."""
    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.12")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")

    assert rs.upgraded_under_us() == "1.2.14"


def test_no_banner_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.14")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")

    assert rs.upgraded_under_us() is None


def test_a_source_checkout_never_asks_for_a_restart(monkeypatch):
    """project_version() answers "dev" outside an installed package.

    Comparing "dev" against anything would nag developers on every launch.
    """
    monkeypatch.setattr(rs, "RUNNING_VERSION", "dev")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")
    assert rs.upgraded_under_us() is None

    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.14")
    monkeypatch.setattr(rs, "installed_version", lambda: "dev")
    assert rs.upgraded_under_us() is None


def test_installed_version_is_re_read_not_remembered(monkeypatch):
    """A cached sys.path scan would hide the upgrade for the life of the process."""
    calls = []
    monkeypatch.setattr(rs.importlib, "invalidate_caches", lambda: calls.append("invalidated"))
    monkeypatch.setattr(rs, "project_version", lambda: "1.2.14")

    assert rs.installed_version() == "1.2.14"
    assert calls == ["invalidated"]


def test_services_are_try_restarted_not_started(monkeypatch):
    """Someone who stopped ASM on purpose must not get it back from an upgrade."""
    seen = {}

    monkeypatch.setattr(rs.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(rs.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd) or SimpleNamespace(returncode=0))

    rs.restart_user_services()

    assert seen["cmd"][:3] == ["/usr/bin/systemctl", "--user", "try-restart"]
    assert "arctis-manager.service" in seen["cmd"]


def test_restarting_services_survives_systemctl_being_absent(monkeypatch):
    """dinit systems, containers: no systemctl, and no reason to raise."""
    monkeypatch.setattr(rs.shutil, "which", lambda name: None)

    def explode(*a, **kw):
        raise AssertionError("subprocess must not be called without systemctl")

    monkeypatch.setattr(rs.subprocess, "run", explode)

    rs.restart_user_services()  # must not raise


def test_a_failing_service_restart_is_not_fatal(monkeypatch):
    """The GUI restart that follows matters more than the services succeeding."""
    monkeypatch.setattr(rs.shutil, "which", lambda name: "/usr/bin/systemctl")

    def boom(*a, **kw):
        raise OSError("no session bus")

    monkeypatch.setattr(rs.subprocess, "run", boom)

    rs.restart_user_services()  # must not raise
