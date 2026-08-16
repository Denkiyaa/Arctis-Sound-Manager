#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find out what keeps an Arctis headset awake when nothing is playing (#180).

Symptom this exists for: the headset never reaches its inactivity timeout and
never powers itself off, because something in the audio graph is feeding it
around the clock. Stopping ASM lets it suspend, so the chain ASM builds is what
holds it.

Two of the three pieces are already confirmed on real hardware: marking the
processing chains `node.passive` lets them suspend, and it does not break the
sound. What is left is the three channel loopbacks (`Arctis_Game_sink_out` and
friends), which stay `running` with nothing playing and keep pushing their
chains.

This script tries the two properties that would stop that, one at a time, and
records what each one does. It edits ASM's installed loopback code, so it needs
root, keeps a backup, and puts everything back with `--revert`. Updating ASM
also replaces the file, which undoes it just as well.

Usage:

    sudo python3 test_idle_suspend.py --variant passive     # try this first
    sudo python3 test_idle_suspend.py --variant pause-on-idle
    sudo python3 test_idle_suspend.py --variant both
    sudo python3 test_idle_suspend.py --revert              # back to normal

After each run: leave the machine quiet with nothing playing, wait for the
report it prints, and check whether the headset finally powers off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ANCHOR = 'f" node.linger=true"'
PROPS = {
    "passive": 'f" node.passive=true"',
    "pause-on-idle": 'f" node.pause-on-idle=true"',
}
SETTLE_SECONDS = 45


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, **kw)


def loopback_manager_path() -> Path:
    """Locate the installed module, without importing it as root."""
    out = run([sys.executable, "-c",
               "import arctis_sound_manager.loopback_manager as m; print(m.__file__)"])
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("Could not find ASM's loopback_manager. Is ASM installed for "
                 "this Python?\n" + (out.stderr or "").strip())
    path = Path(out.stdout.strip())
    if not path.is_file():
        sys.exit(f"{path} does not exist.")
    return path


def backup_of(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".asm-idle-test.bak")


def restore(path: Path) -> bool:
    bak = backup_of(path)
    if not bak.is_file():
        return False
    shutil.copy2(bak, path)
    bak.unlink()
    for pyc in path.parent.glob("__pycache__/loopback_manager*.pyc"):
        pyc.unlink(missing_ok=True)
    return True


def apply_variant(path: Path, variant: str) -> None:
    restore(path)  # always start from the untouched file
    text = path.read_text(encoding="utf-8")
    if ANCHOR not in text:
        sys.exit(f"Could not find the line to patch in {path}.\nThis script is "
                 "older than your ASM version; please say so on issue #180.")
    wanted = ["passive", "pause-on-idle"] if variant == "both" else [variant]
    indent = re.search(rf"^(\s*){re.escape(ANCHOR)}", text, re.M).group(1)
    addition = "".join(f"\n{indent}{PROPS[k]}" for k in wanted)
    shutil.copy2(path, backup_of(path))
    path.write_text(text.replace(ANCHOR, ANCHOR + addition, 1), encoding="utf-8")
    for pyc in path.parent.glob("__pycache__/loopback_manager*.pyc"):
        pyc.unlink(missing_ok=True)


def user_ctl(*args: str) -> subprocess.CompletedProcess:
    """systemctl --user, usable from a sudo shell.

    The daemon is a user unit, so it must be addressed as the logged-in user
    rather than as root, which has its own empty session bus.
    """
    user = os.environ.get("SUDO_USER")
    if user:
        uid = run(["id", "-u", user]).stdout.strip()
        env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}",
               "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
        return run(["sudo", "-u", user, "-E", "systemctl", "--user", *args], env=env)
    return run(["systemctl", "--user", *args])


def restart_stack() -> None:
    """Restart the daemon and clear the orphaned loopbacks.

    pw-loopback runs with node.linger=true, so the processes outlive the
    daemon by design. Without clearing them the old ones keep running with
    the old properties and the test measures nothing.
    """
    print("  restarting arctis-manager ...")
    user_ctl("restart", "arctis-manager")
    time.sleep(2)
    print("  clearing orphaned pw-loopback processes ...")
    run(["pkill", "-f", "pw-loopback"])
    time.sleep(1)
    user_ctl("restart", "arctis-manager")
    time.sleep(5)


def loopback_cmdlines() -> list[str]:
    out = run(["pgrep", "-a", "pw-loopback"])
    return [l for l in out.stdout.splitlines() if l.strip()]


def verify_applied(variant: str) -> bool:
    """Confirm the property really reached the running processes."""
    lines = loopback_cmdlines()
    if not lines:
        print("  ✗ no pw-loopback processes are running at all.")
        return False
    wanted = ["node.passive=true"] if variant == "passive" else \
             ["node.pause-on-idle=true"] if variant == "pause-on-idle" else \
             ["node.passive=true", "node.pause-on-idle=true"]
    ok = all(all(w in l for w in wanted) for l in lines)
    print(f"  {'✓' if ok else '✗'} {len(lines)} pw-loopback process(es), "
          f"property {'present on all' if ok else 'MISSING'}")
    if not ok:
        print("     " + lines[0][:200])
    return ok


def node_states() -> list[tuple[str, str]]:
    if shutil.which("pw-dump") is None:
        return []
    try:
        dump = json.loads(run(["pw-dump"]).stdout)
    except Exception:
        return []
    rows = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        name = props.get("node.name", "?")
        mclass = props.get("media.class", "")
        if "Audio" not in mclass:
            continue
        interesting = (name.startswith(("alsa_output.", "bluez_output."))
                       or "Arctis_" in name or "sonar-" in name
                       or "virtual-surround" in name)
        if interesting:
            rows.append((info.get("state", "?"), name))
    return sorted(rows, key=lambda r: r[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["passive", "pause-on-idle", "both"])
    ap.add_argument("--revert", action="store_true",
                    help="restore the original file and restart, then exit")
    args = ap.parse_args()

    if not args.revert and not args.variant:
        ap.error("give --variant passive | pause-on-idle | both, or --revert")
    if os.geteuid() != 0:
        sys.exit("This edits ASM's installed files, so it needs root:\n"
                 f"    sudo python3 {sys.argv[0]} "
                 + ("--revert" if args.revert else f"--variant {args.variant}"))

    path = loopback_manager_path()

    if args.revert:
        if restore(path):
            print(f"Restored {path}")
        else:
            print("Nothing to restore; the file is already untouched.")
        restart_stack()
        print("Done. ASM is back to its released behaviour.")
        return 0

    print(f"== testing node.{args.variant} on the channel loopbacks ==\n")
    print(f"Patching {path}")
    apply_variant(path, args.variant)
    restart_stack()

    if not verify_applied(args.variant):
        print("\nThe property did not reach the running processes, so the "
              "measurement below would be meaningless.\nPlease paste this "
              "output on issue #180 as it is.")

    print(f"\nNow leave the machine quiet: stop any music, video, game or call, "
          f"and\ndo not touch it for {SETTLE_SECONDS} seconds while the graph "
          f"settles.\n")
    for remaining in range(SETTLE_SECONDS, 0, -5):
        print(f"  {remaining:>3}s ...", end="\r", flush=True)
        time.sleep(5)
    print(" " * 30, end="\r")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [
        f"# ASM idle test — variant={args.variant} — {stamp}",
        "",
        "== node states with nothing playing ==",
        "(suspended/idle = not holding the device; running = holding it)",
        "",
    ]
    rows = node_states()
    if rows:
        lines += [f"  {state:<10} {name}" for state, name in rows]
    else:
        lines.append("  (pw-dump unavailable)")
    lines += ["", "== pw-loopback command lines ==", ""]
    lines += [f"  {l}" for l in loopback_cmdlines()] or ["  (none running)"]

    report = "\n".join(lines)
    print(report)

    out = Path.home() / f"asm-idle-test-{args.variant}-{stamp}.txt"
    if os.environ.get("SUDO_USER"):
        out = Path(f"/home/{os.environ['SUDO_USER']}") / out.name
    try:
        out.write_text(report + "\n", encoding="utf-8")
        if os.environ.get("SUDO_USER"):
            shutil.chown(out, os.environ["SUDO_USER"])
        print(f"\nSaved to {out}")
    except OSError as exc:
        print(f"\n(could not save a copy: {exc})")

    print("\n== what to do now ==")
    print("1. Leave the headset alone and see whether it finally powers off.")
    print("2. Check that sound still works normally afterwards.")
    print(f"3. Attach {out.name} to issue #180 with the answer to 1 and 2.")
    print(f"\nTo undo:  sudo python3 {sys.argv[0]} --revert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
