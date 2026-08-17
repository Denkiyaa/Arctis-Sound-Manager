# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Debian is built two ways, and both have to ship the same thing.

`debian/rules` builds the PPA package; `debian/build-deb.sh` builds the .deb
attached to GitHub releases (.github/workflows/release.yaml). They install the
same payload by hand, in two places, and nothing kept them in step.

That is how arctis-stream-guard reached the RPM, the PKGBUILD, debian/rules,
debian/postrm, the Nix module and the dinit templates — and not the .deb people
download from the releases page. The service simply did not exist for those
users, with nothing anywhere to say so.

This test does not care which files are shipped, only that the two paths agree
on the ones they share. build-deb.sh legitimately installs more (dinit
templates, the first-run desktop entry, the generated udev rules); anything
debian/rules installs and it does not is a package missing a piece.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_INSTALL = re.compile(r"install -Dm\d+ ([\w./-]+)")


def _installed_sources(path: Path) -> set[str]:
    """Repo-relative sources each build path installs."""
    return set(_INSTALL.findall(path.read_text()))


def test_the_release_deb_ships_everything_the_ppa_deb_does():
    rules = _installed_sources(REPO / "debian" / "rules")
    script = _installed_sources(REPO / "debian" / "build-deb.sh")

    missing = sorted(rules - script)
    assert not missing, (
        "debian/rules installs these and debian/build-deb.sh does not, so they "
        "are absent from the .deb on the releases page while the PPA has them: "
        + ", ".join(missing))


def test_every_systemd_unit_in_the_tree_reaches_both_debian_paths():
    """A new unit must not be added to one build path only."""
    units = {p.name for p in (REPO / "systemd").glob("*.service")}
    rules = (REPO / "debian" / "rules").read_text()
    script = (REPO / "debian" / "build-deb.sh").read_text()

    for unit in sorted(units):
        assert unit in rules, f"{unit} is not installed by debian/rules"
        assert unit in script, f"{unit} is not installed by debian/build-deb.sh"


def test_every_entry_point_becomes_an_executable_in_both_debian_paths():
    """The gap the first version of this test could not see.

    debian/rules writes each console script with printf and build-deb.sh
    generated them from a hand-kept list, so neither shows up as an `install`
    line — the source-file comparison above walked straight past them. The list
    in build-deb.sh had five of the eight entry points: the .deb on the
    releases page shipped a stream-guard unit and a clip keybinding pointing at
    /usr/bin executables that were not in the package.

    Both paths must account for every name in [project.scripts]; how they
    produce it is their business.
    """
    scripts = _entry_points()
    assert scripts, "no [project.scripts] found — the parser needs updating"

    rules = (REPO / "debian" / "rules").read_text()
    script = (REPO / "debian" / "build-deb.sh").read_text()

    # build-deb.sh reads the [project.scripts] table itself, so it accounts
    # for all of them at once. Matching on the table name rather than on
    # "pyproject.toml": the script already reads that file for the version,
    # so the looser marker was true even for the hand-kept list this test
    # exists to reject.
    derived = "project.scripts" in script or "project\\.scripts" in script

    # asm-diag-dinit is the odd one out on purpose: it diagnoses a dinit setup,
    # and debian/rules builds for the PPA, which targets systemd distributions.
    # build-deb.sh ships it because that .deb also reaches dinit systems.
    systemd_only_exception = {"asm-diag-dinit"}

    for name in sorted(scripts - systemd_only_exception):
        assert name in rules, f"{name} is never installed by debian/rules"
        assert derived or name in script, (
            f"{name} is in [project.scripts] but debian/build-deb.sh neither "
            f"names it nor derives its list from pyproject.toml")


def _entry_points() -> set[str]:
    text = (REPO / "pyproject.toml").read_text()
    block = re.search(r"^\[project\.scripts\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not block:
        return set()
    return {m.group(1) for m in re.finditer(r"^([\w-]+)\s*=", block.group(1), re.M)}
