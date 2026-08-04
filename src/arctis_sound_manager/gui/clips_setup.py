# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""clips_setup.py — the one place that installs, removes and reports Clips.

Three screens ask the same questions: the Settings row, the Video tab's install
page, and the recorder's own Uninstall. Before this module each carried its own
copy of the ``pkexec`` batch, and the copies had already drifted — one of them
re-probed after installing and one trusted the exit code. Nothing here draws
anything: callers own their dialogs and their status text, so the same answers
can be rendered as a settings row or as a full page.

Deliberately free of any ``gi`` / GStreamer import: this runs on machines where
the whole point is that none of that is installed yet.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger("ClipsSetup")

# Long enough for a slow mirror to deliver the GStreamer sets; short enough that
# a package manager waiting on a lock it will never get does not hang the GUI
# for the rest of the session.
_BATCH_TIMEOUT_S = 900


# ── what the machine has ──────────────────────────────────────────────────────

def missing_checks() -> list:
    """Every Clips dependency this machine does not have.

    A probe that raises counts as missing: the question being answered is "can
    this record?", and a check that cannot answer has not said yes.
    """
    from arctis_sound_manager.system_deps_checker import clip_dep_checks

    missing = []
    for check in clip_dep_checks():
        try:
            ok = bool(check.detect())
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            missing.append(check)
    return missing


def present_names() -> set[str]:
    """The names of the dependency groups this machine currently has.

    Compared before and after a removal, this is how the outcome is worked out
    without asking each distro's package manager what it did: a group that has
    stopped being detected is a group that went. Cheaper than parsing three
    package managers' output, and it reports what is *true* rather than what a
    command claimed.
    """
    from arctis_sound_manager.system_deps_checker import clip_dep_checks

    present = set()
    for check in clip_dep_checks():
        try:
            if check.detect():
                present.add(check.name)
        except Exception:  # noqa: BLE001
            pass
    return present


def blocking_missing() -> list:
    """The missing checks that stop it recording at all — the ones that decide
    whether the feature works, as opposed to the ones that cost a thumbnail."""
    from arctis_sound_manager.system_deps_checker import Severity

    return [c for c in missing_checks() if c.severity is Severity.BLOCKING]


def runtime_ready() -> bool:
    """True when every blocking package Clips needs is present."""
    try:
        return not blocking_missing()
    except Exception as exc:  # noqa: BLE001 — never let a probe hide the feature
        logger.debug("runtime probe failed, assuming not ready: %s", exc)
        return False


def clips_enabled() -> bool:
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().clips_enabled)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read clips_enabled, reading as off: %s", exc)
        return False


def clips_active() -> bool:
    """Whether the Video tab should show the recorder rather than the install
    screen.

    Both halves are required, and each catches a state the other misses. The
    runtime alone would keep showing the recorder to someone who has just
    uninstalled Clips but kept ffmpeg — which the rest of their desktop needs —
    making Uninstall look broken. The flag alone would show the recorder on a
    machine whose packages were removed from underneath it.
    """
    return clips_enabled() and runtime_ready()


def set_enabled(value: bool) -> None:
    """Persist the opt-in flag. Failing to write it is worth a log and nothing
    more: the caller has already done the part the user asked for."""
    try:
        from arctis_sound_manager.settings import GeneralSettings
        settings = GeneralSettings.read_from_file()
        settings.clips_enabled = bool(value)
        settings.write_to_file()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist clips_enabled=%s: %s", value, exc)


# ── the commands ──────────────────────────────────────────────────────────────

def install_argvs(checks: list | None = None) -> list[list[str]]:
    """Package-manager argv for the given checks (missing ones by default)."""
    from arctis_sound_manager.system_deps_checker import install_command_for

    if checks is None:
        checks = missing_checks()
    return [cmd for cmd in (install_command_for(c) for c in checks) if cmd]


def remove_argvs() -> tuple[list[list[str]], list[str]]:
    """Removal argv for the Clips group, and the package names they name.

    The names are returned so a caller can say out loud what is about to be
    removed — these packages are shared with the rest of the desktop, and the
    user is the only one who knows whether anything else here needs ffmpeg.
    """
    from arctis_sound_manager.system_deps_checker import (clip_dep_checks,
                                                          remove_command_for)

    argvs: list[list[str]] = []
    packages: list[str] = []
    for check in clip_dep_checks():
        cmd = remove_command_for(check)
        if not cmd:
            continue
        argvs.append(cmd)
        packages.extend(a for a in cmd[3:] if not a.startswith("-"))
    return argvs, sorted(set(packages))


def packages_in(argv: list[str]) -> list[str]:
    """The package names in a package-manager argv, dropping the manager, the
    subcommand and any flags: ``apt-get install -y A B`` → ``[A, B]``."""
    return [tok for tok in argv[2:] if not tok.startswith("-")]


def manual_command(argvs: list[list[str]]) -> str | None:
    """One copy-pasteable command for a whole group of argv, or None when there
    is nothing to run.

    Shown before anything is elevated, for installing and for removing alike.
    Someone about to hand a GUI their root password is entitled to see the
    command it is going to run — and on the removal side that is not a courtesy:
    these are packages the rest of the desktop shares, and the exact line is
    what lets a user check it against their own machine before agreeing, or run
    it themselves with the flags they would rather use.

    The confirmation flags are dropped along with the rest: a command a person
    is going to read and run should stop and ask them, which is exactly what
    `--noconfirm` exists to prevent.
    """
    if not argvs:
        return None
    base = argvs[0][:2]  # e.g. ["pacman", "-S"] / ["apt-get", "install"]
    pkgs: list[str] = []
    for argv in argvs:
        for p in packages_in(argv):
            if p not in pkgs:
                pkgs.append(p)
    if not pkgs:
        return None
    return "sudo " + " ".join(base + pkgs)


# ── running them ──────────────────────────────────────────────────────────────

class NoPkexec(RuntimeError):
    """polkit is not installed, so nothing can be elevated from here."""


# What each package manager says when the install cannot proceed because the
# machine's packages and its repositories disagree — the state Arch calls a
# partial upgrade, and the one a user on a derivative distro is most likely to
# hit here: the repo's gst-plugin-pipewire wants an exact pipewire release, and
# the installed one is the derivative's rebuild of the same upstream version.
_CONFLICT_MARKERS = (
    "could not satisfy dependencies",   # pacman
    "breaks dependency",                # pacman
    "unmet dependencies",               # apt
    "held broken packages",             # apt
    "nothing provides",                 # dnf
    "conflicting requests",             # dnf
)


def looks_like_dependency_conflict(output: str) -> bool:
    """Whether a failed install failed because the machine is mid-upgrade.

    Worth telling apart from every other install failure: nothing the user can
    do on this screen will fix it, and the fix — update the whole system first —
    is not one they would guess from "installing pipewire breaks dependency
    'pipewire=1:1.6.8-1.2' required by pipewire-pulse".
    """
    low = output.lower()
    return any(marker in low for marker in _CONFLICT_MARKERS)


def last_line(output: str) -> str:
    """The most specific line of a package manager's complaint."""
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_batch(argvs: list[list[str]], keep_going: bool = False) -> tuple[bool, str]:
    """Run package commands as one elevated batch; return (ok, detail).

    One ``pkexec`` for the whole batch so the password is asked once rather than
    once per package group. Synchronous because the button has nothing useful to
    offer while it waits, and the result decides whether the feature is on.

    *keep_going* is what removal needs and installing does not. Removing runs
    commands the package manager is *expected* to refuse — every one of these
    packages is shared, and a refusal means something else on the machine still
    needs it, which is the outcome we want rather than an error. Chained with
    ``&&`` the first such refusal abandoned the rest, so on a desktop where
    anything at all depends on ffmpeg, nothing was ever removed: the first
    command in the list took the whole batch down with it. Independent commands
    let each package be judged on its own; the caller re-probes to find out what
    actually went.

    Only ever hand this argv built from the Clips dependency group. It used to
    be reachable with an ``_internal`` remediation, which is how turning on a
    screen recorder ended up able to restart the audio stack.
    """
    if not argvs:
        return True, ""
    if not shutil.which("pkexec"):
        raise NoPkexec

    def _quote(args: list[str]) -> str:
        return " ".join(f"'{a}'" if " " in a else a for a in args)

    joiner = "; " if keep_going else " && "
    try:
        proc = subprocess.run(
            ["pkexec", "sh", "-c", joiner.join(_quote(a) for a in argvs)],
            capture_output=True, text=True, timeout=_BATCH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("clip package command failed: %s", exc)
        return False, str(exc)

    # With keep_going the exit status is the last command's and says nothing
    # about the others, so it is not an answer worth reporting — except for the
    # one failure that matters here, an authentication the user dismissed.
    if keep_going:
        if proc.returncode == 126:  # pkexec: not authorised / dialog dismissed
            return False, (proc.stderr or "").strip()
        return True, ""

    if proc.returncode != 0:
        # The whole complaint, not its last line: the line that names the cause
        # is rarely the last one a package manager prints, and the caller has to
        # be able to tell a dependency conflict from a mirror that timed out.
        return False, (proc.stderr or proc.stdout or "").strip()
    return True, ""
