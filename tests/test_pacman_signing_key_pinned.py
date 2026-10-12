# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""#322 regression.

The pacman repository was signed with whatever key `head -1` found in the
shared GPG_PRIVATE_KEY secret, and every installer trusted whatever key the
release served. When that secret was narrowed to the Launchpad key for the
PPA (#270), v1.6.0 went out signed with the key retired in #239, the published
.key followed it, and nothing anywhere failed.

The signing key is now pinned by fingerprint: the workflow refuses to sign
with any other, and every place that tells pacman what to trust lsigns that
fingerprint instead of reading it out of the downloaded file. This keeps all
of them on the same key, and keeps the read-it-from-the-file idiom out.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pacman-repo.yaml"

TRUSTING_FILES = [
    "docs/install.sh",
    "scripts/distrobox/_common.sh",
    "scripts/distrobox/steamos.sh",
    "scripts/distrobox/bazzite.sh",
    "src/arctis_sound_manager/update_checker.py",
    "README.md",
    ".github/workflows/pacman-repo.yaml",
]


def _pinned_fingerprint() -> str:
    m = re.search(r'PACMAN_KEY_FINGERPRINT="([0-9A-F]{40})"', WORKFLOW.read_text())
    assert m, "pacman-repo.yaml no longer pins PACMAN_KEY_FINGERPRINT"
    return m.group(1)


def test_workflow_signs_with_its_own_secret_and_checks_the_key():
    text = WORKFLOW.read_text()
    assert "secrets.PACMAN_GPG_PRIVATE_KEY" in text
    assert "secrets.GPG_PRIVATE_KEY" not in text
    assert 'gpg --list-secret-keys "$PACMAN_KEY_FINGERPRINT"' in text
    step = text.split("- name: Import signing key")[1].split("- name:")[0]
    code = "\n".join(l for l in step.splitlines() if not l.strip().startswith("#"))
    assert "head -1" not in code


@pytest.mark.parametrize("rel", TRUSTING_FILES)
def test_every_trust_path_lsigns_the_pinned_fingerprint(rel):
    text = (REPO / rel).read_text()
    fpr = _pinned_fingerprint()
    lsigns = re.findall(r"--lsign-key\s+(\S+)", text)
    assert lsigns, f"{rel} no longer lsigns the repository key"
    for target in lsigns:
        target = target.strip("\"';")
        assert target in (fpr, "$PACMAN_KEY_FINGERPRINT"), (
            f"{rel} lsigns {target!r}, not the pinned key {fpr}"
        )
    assert "gpg --show-keys --with-colons /tmp/asm.key | awk" not in text


def test_installer_pins_the_same_fingerprint():
    text = (REPO / "docs" / "install.sh").read_text()
    assert f'PACMAN_KEY_FINGERPRINT="{_pinned_fingerprint()}"' in text
