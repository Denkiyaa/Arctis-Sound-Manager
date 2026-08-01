# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Packaging manifests must stay in sync with what the project actually ships.

Adding a console script or a service unit means touching five packaging files
that no test ever read, so the omissions were silent and each failed in its own
way, far from the change that caused them:

* the RPM installs the wheel with ``python3 -m installer``, so a console script
  missing from ``%files`` is an *unpackaged file* — the build fails outright;
* ``debian/rules`` hand-writes each console script, so one missing there is a
  command that simply does not exist after ``apt install``;
* a systemd unit missing from a manifest is a service that never starts, with
  nothing in any log to say why.

These tests read the manifests as text on purpose. They are shell, Makefile and
RPM spec, not data files, and the failure being guarded against is a forgotten
line — which a substring check catches exactly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SPEC = ROOT / "arctis-sound-manager.spec"
PKGBUILD = ROOT / "aur" / "PKGBUILD"
DEB_RULES = ROOT / "debian" / "rules"


def _console_scripts() -> dict[str, str]:
    """``{command: "module:function"}`` from ``[project.scripts]``.

    Parsed by hand rather than with ``tomllib``: the project still supports
    Python 3.10, where it does not exist, and this one table is plain
    ``key = "value"`` lines.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[)", text,
                      re.MULTILINE | re.DOTALL)
    assert block, "[project.scripts] not found in pyproject.toml"
    scripts = dict(re.findall(r'^\s*([\w.-]+)\s*=\s*"([^"]+)"\s*$',
                              block.group(1), re.MULTILINE))
    assert scripts, "[project.scripts] parsed as empty — parser out of step"
    return scripts


def _spec_files_section() -> str:
    """Just the %files section — %install mentions the same paths, and a line
    landing in the wrong section is one of the mistakes being guarded against."""
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("\n%files")
    end = text.index("\n%changelog", start)
    return text[start:end]


def _systemd_units() -> list[str]:
    return sorted(p.name for p in (ROOT / "systemd").glob("*.service"))


def _dinit_units() -> list[str]:
    return sorted(p.name for p in (ROOT / "dinit").iterdir() if p.is_file())


# ── console scripts ───────────────────────────────────────────────────────

@pytest.mark.parametrize("script", sorted(_console_scripts()))
def test_console_script_is_packaged_in_rpm(script):
    """A wheel-generated binary absent from %files fails the rpmbuild."""
    assert f"%{{_bindir}}/{script}" in _spec_files_section(), (
        f"{script} is in [project.scripts] but not in the spec's %files — "
        f"rpmbuild will fail with 'Installed (but unpackaged) file(s) found'"
    )


# asm-diag-dinit diagnoses a dinit installation, and the Debian package ships
# nothing dinit-related — no service templates, no dependency. Leaving the
# diagnostic out of the .deb is deliberate, not an oversight. The companion test
# below keeps that justification honest: if debian/ ever starts packaging dinit,
# the exemption has to be revisited rather than quietly carried forward.
_DEBIAN_EXEMPT = {"asm-diag-dinit"}


@pytest.mark.parametrize("script", sorted(set(_console_scripts()) - _DEBIAN_EXEMPT))
def test_console_script_is_written_by_debian_rules(script):
    """debian/rules writes each entry point by hand; a gap means no command."""
    assert f"/usr/bin/{script}" in DEB_RULES.read_text(encoding="utf-8"), (
        f"{script} is in [project.scripts] but debian/rules never writes it — "
        f"the command will not exist in the .deb"
    )


def test_debian_dinit_exemption_is_still_justified():
    """The exemption above holds only while debian/ ignores dinit entirely."""
    rules = DEB_RULES.read_text(encoding="utf-8")
    assert "dinit" not in rules, (
        "debian/rules now references dinit — reconsider whether asm-diag-dinit "
        "still belongs in _DEBIAN_EXEMPT"
    )


def test_debian_rules_targets_the_right_module():
    """The hand-written stubs must import the module pyproject points at."""
    rules = DEB_RULES.read_text(encoding="utf-8")
    for script, target in _console_scripts().items():
        if script in _DEBIAN_EXEMPT:
            continue
        module = target.split(":")[0]
        line = next((ln for ln in rules.splitlines()
                     if ln.rstrip().endswith(f"/usr/bin/{script}")), None)
        assert line is not None, f"no debian/rules line writes {script}"
        assert module in line, (
            f"debian/rules writes {script} from '{line.strip()}' but "
            f"pyproject.toml points it at {module}"
        )


# ── systemd units ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit", _systemd_units())
def test_systemd_unit_is_installed_by_every_packaging(unit):
    for name, path in (("spec", SPEC), ("PKGBUILD", PKGBUILD), ("debian/rules", DEB_RULES)):
        assert f"systemd/{unit}" in path.read_text(encoding="utf-8"), (
            f"{unit} exists in systemd/ but {name} never installs it"
        )


@pytest.mark.parametrize("unit", _systemd_units())
def test_systemd_unit_is_listed_in_spec_files(unit):
    assert f"%{{_userunitdir}}/{unit}" in _spec_files_section(), (
        f"{unit} is installed by the spec but missing from %files"
    )


# ── dinit units ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit", _dinit_units())
def test_dinit_unit_is_installed_by_rpm_and_arch(unit):
    for name, path in (("spec", SPEC), ("PKGBUILD", PKGBUILD)):
        assert f"dinit/{unit}" in path.read_text(encoding="utf-8"), (
            f"{unit} exists in dinit/ but {name} never installs it"
        )


# ── spec hygiene ──────────────────────────────────────────────────────────

def test_spec_install_section_has_no_stray_rpm_macros():
    """A `%{_userunitdir}/…` line loose in %install is not an install command —
    it is a shell line that fails the build. This is what a careless
    search-and-replace produces, and rpmbuild is the only thing that notices."""
    text = SPEC.read_text(encoding="utf-8")
    install = text[text.index("\n%install"):text.index("\n%check")
                   if "\n%check" in text else text.index("\n%post")]
    offenders = [
        ln for ln in install.splitlines()
        if re.match(r"^%\{_(bindir|userunitdir|datadir|udevrulesdir)\}", ln.strip())
    ]
    assert not offenders, (
        "these look like %files entries that landed in %install: " + repr(offenders)
    )
