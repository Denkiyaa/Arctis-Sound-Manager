# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stop stale copies of packaged device profiles from freezing a headset.

`asm-setup` used to copy every packaged device profile into
``~/.config/arctis_manager/devices/`` on its first (and only) run, and that
folder takes precedence over the packaged one — ``DEVICES_CONFIG_FOLDER`` is
``[HOME, SRC]`` and the first match wins. A package upgrade replaces the
packaged profiles and never touches the copies, and nothing re-runs asm-setup
afterwards (it is wired to `FirstRunDialog`, first GUI launch only, and could
not run from a root post-install anyway since it writes to `$HOME`).

The consequence was silent and permanent: **every profile fix shipped after a
user's first run was inert for them.** New product ids — a headset ASM had just
learned to drive — status byte offsets, EQ opcodes, all of it. The user
upgrades, sees the new version number, and runs last year's protocol. It cost
this project an evening of chasing a firmware that was innocent, on the
maintainer's own machine, and it fits a family of "I updated and it still does
not work" reports.

Three rules, applied on every daemon start so existing installs are repaired
rather than only new ones spared:

1. **A copy identical to the packaged profile is deleted.** Byte-identical
   means it provably carries nothing of the user's, and its only effect is to
   pin that family to today's content forever.
2. **A copy that differs is moved aside**, to ``devices-disabled/``, and
   reported loudly. It cannot be told apart from a deliberate override — both
   are "the packaged file with edits" — and leaving it loaded is what keeps
   updates from landing. Nothing is destroyed: the file stays on disk, the log
   says where it went and why.
3. **A profile with no packaged counterpart is left strictly alone.** That is
   someone adding a family ASM does not ship, which is the one thing this
   folder is genuinely for.

Rule 2 is the deliberate trade. A user who hand-added a product id loses it
until they re-apply, and is told so by name; the alternative is that nobody's
profile fixes ever reach them. Rule 3 keeps the case worth protecting safe.
Merging an override onto the packaged profile — so "add a product id" stops
meaning "freeze this family" — is the proper fix and is a separate change.
"""
from __future__ import annotations

import filecmp
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger('DeviceOverrides')

#: Where rule 2 parks a profile it stopped loading.
DISABLED_DIRNAME = 'devices-disabled'


@dataclass
class ReconcileReport:
    """What a pass did, so callers can log or surface it."""
    removed: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed or self.disabled)


def reconcile_home_device_overrides(home_dir: Path, packaged_dir: Path,
                                    dry_run: bool = False) -> ReconcileReport:
    """Apply the three rules above to *home_dir* against *packaged_dir*.

    Idempotent: once a pass has run, nothing in *home_dir* shadows a packaged
    profile, so later passes are no-ops. Never raises — a profile folder that
    cannot be tidied must not stop the daemon from starting; the worst case is
    the status quo.
    """
    report = ReconcileReport()

    try:
        if not home_dir.is_dir() or not packaged_dir.is_dir():
            return report

        for override in sorted(home_dir.glob('*.yaml')):
            packaged = packaged_dir / override.name

            # Rule 3: a family ASM does not ship. Not ours to touch.
            if not packaged.is_file():
                report.kept.append(override.name)
                continue

            try:
                identical = filecmp.cmp(override, packaged, shallow=False)
            except OSError as exc:
                logger.warning('could not compare %s with the packaged profile: %r',
                               override.name, exc)
                report.kept.append(override.name)
                continue

            # Rule 1: a pure copy. Nothing of the user's is in it.
            if identical:
                report.removed.append(override.name)
                if not dry_run:
                    try:
                        override.unlink()
                    except OSError as exc:
                        logger.warning('could not remove the redundant copy %s: %r',
                                       override.name, exc)
                        report.removed.remove(override.name)
                        report.kept.append(override.name)
                continue

            # Rule 2: differs — park it, keep it, say so.
            report.disabled.append(override.name)
            if not dry_run:
                disabled_dir = home_dir.parent / DISABLED_DIRNAME
                try:
                    disabled_dir.mkdir(parents=True, exist_ok=True)
                    override.replace(disabled_dir / override.name)
                except OSError as exc:
                    logger.warning('could not move %s aside: %r', override.name, exc)
                    report.disabled.remove(override.name)
                    report.kept.append(override.name)
    except Exception as exc:  # never block startup over housekeeping
        logger.warning('device override reconciliation failed: %r', exc)

    return report


def log_report(report: ReconcileReport, home_dir: Path) -> None:
    """Explain what happened, in terms a user can act on."""
    if report.removed:
        logger.info(
            'Removed %d redundant device profile cop%s from %s (identical to the '
            'packaged profiles, and shadowing them would have pinned those '
            'headsets to an old protocol on the next upgrade): %s',
            len(report.removed), 'y' if len(report.removed) == 1 else 'ies',
            home_dir, ', '.join(report.removed),
        )

    for name in report.disabled:
        logger.warning(
            '%s differed from the packaged profile and was NOT being updated by '
            'upgrades, so it has been moved to %s and is no longer loaded. '
            'ASM now uses its own profile for that headset. If you had added a '
            'product id or changed a setting there, re-apply it to the moved '
            'copy and open an issue so the change ships for everyone.',
            name, home_dir.parent / DISABLED_DIRNAME / name,
        )

    if report.kept:
        logger.info('Device profiles kept as user-authored (no packaged '
                    'counterpart): %s', ', '.join(report.kept))
