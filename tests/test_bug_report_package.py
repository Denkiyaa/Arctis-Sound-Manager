# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_bug_report_package.py — a report must say which package it came from.

ASM is redistributed under other names — Fedora's Terra ships it as
python3-arctis-sound-manager — and those builds don't necessarily declare the
same dependencies as ours. A crash on a missing `pactl`, or silent spatial
audio, can come from the packaging rather than the code (#117, #88). Knowing
the owning package and its vendor answers "is this our build?" before anyone
starts reading the code.
"""
from __future__ import annotations

from types import SimpleNamespace

from arctis_sound_manager import bug_reporter


def test_rpm_report_names_package_version_and_vendor(monkeypatch):
    monkeypatch.setattr(bug_reporter.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=0,
        stdout="python3-arctis-sound-manager 1.2.13-1.fc44 vendor=Terra\n"))

    result = bug_reporter._owning_package()

    assert "python3-arctis-sound-manager" in result
    assert "vendor=Terra" in result
    assert result.startswith("owned-by[rpm]=")


def test_dpkg_output_is_reduced_to_the_package_name(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[0] != "dpkg":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0,
                               stdout="arctis-sound-manager: /usr/lib/x.py\n")

    monkeypatch.setattr(bug_reporter.subprocess, "run", fake_run)

    assert bug_reporter._owning_package() == "owned-by[dpkg]=arctis-sound-manager"


def test_no_owning_package_is_not_an_error(monkeypatch):
    """pip, pipx or a source checkout: nothing owns the file."""
    monkeypatch.setattr(bug_reporter.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout=""))

    assert bug_reporter._owning_package() is None


def test_install_methods_carry_the_owner(monkeypatch):
    monkeypatch.setattr(bug_reporter, "_owning_package",
                        lambda: "owned-by[rpm]=python3-arctis-sound-manager 1.2.13 vendor=Terra")
    monkeypatch.setattr(bug_reporter.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout=""))
    monkeypatch.setattr(bug_reporter.shutil, "which", lambda name: None)

    methods = bug_reporter._detect_install_methods()

    assert any("vendor=Terra" in m for m in methods)


def test_diagnose_reports_the_package(monkeypatch):
    from arctis_sound_manager import diagnose
    import json

    monkeypatch.setattr(bug_reporter, "_owning_package",
                        lambda: "owned-by[rpm]=python3-arctis-sound-manager vendor=Terra")

    info = json.loads(diagnose._section_versions())

    assert "vendor=Terra" in info["package"]
