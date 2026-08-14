#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture why a headset never reaches its inactivity timeout while ASM runs.

Symptom this exists for (issue #180): with nothing playing, every Arctis
virtual channel reads IDLE but the physical sink stays RUNNING, so the
firmware's auto-off timer never starts. Stop ASM and the headset powers down
normally.

The ordinary bug report does not answer the question that matters here. It
lists the state of the *sinks*, but ASM's chain is

    Arctis_Game/Chat/Media -> sonar-*-eq -> hesuvi -> sonar-output-eq -> device

and what has to be known is which node in that chain is still running, and
which links still hold the device. That is per-node state plus the link graph,
neither of which the report carries.

It also captures the same picture with ASM stopped. Without that comparison
there is no way to tell a node ASM keeps alive from one that behaves the same
either way: the machine this was first seen on has a second, unrelated output
device that also reads RUNNING with nothing playing, which is exactly the sort
of detail that turns a confident diagnosis into a wrong one.

Read-only with one exception, clearly announced: it offers to stop and restart
the ASM services to take the comparison. Nothing else is written, no setting
is changed, and the services are restarted before it exits (including on
Ctrl-C).

Usage:
    python3 collect_idle_state.py            # full capture, asks before stopping ASM
    python3 collect_idle_state.py --no-stop  # capture with ASM running only
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Nodes ASM owns, by name fragment.
_ASM_FRAGMENTS = (
    "Arctis_", "sonar-", "virtual-surround", "effect_input", "effect_output",
)
# ...but a real ALSA device is never one of ours, however it is named. Without
# this the headset's own sink ("alsa_output.usb-SteelSeries_Arctis_7_-00")
# matches "Arctis_" and gets flagged as an ASM node, which is the one thing
# this report must not confuse: the whole question is what ASM holds and what
# the hardware does on its own.
_DEVICE_PREFIXES = ("alsa_output.", "alsa_input.", "bluez_output.", "bluez_input.")


def _is_asm_node(name: str) -> bool:
    if name.startswith(_DEVICE_PREFIXES):
        return False
    return any(f in name for f in _ASM_FRAGMENTS)
_SERVICES = ("arctis-manager", "filter-chain", "arctis-video-router")

_out_lines: list[str] = []


def out(line: str = "") -> None:
    print(line)
    _out_lines.append(line)


def run(argv: list[str], timeout: int = 15) -> str:
    """Run a command, returning stdout+stderr. Never raises."""
    if shutil.which(argv[0]) is None:
        return f"({argv[0]} not installed)"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return f"(failed: {e!r})"


def pw_dump() -> list:
    raw = run(["pw-dump"], timeout=30)
    try:
        return json.loads(raw)
    except Exception:
        return []


def _node_rows(dump: list) -> list[tuple[int, str, str, str]]:
    """(id, name, media.class, state) for every audio node in the graph."""
    rows = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info", {}) or {}
        props = info.get("props", {}) or {}
        name = props.get("node.name", "?")
        mclass = props.get("media.class", "")
        # Substring, not startswith: the nodes that matter most here are
        # "Stream/Output/Audio" (effect_output.*, Arctis_*_sink_out), and a
        # startswith("Audio") filter drops every one of them.
        if "Audio" not in mclass:
            continue
        rows.append((obj.get("id", -1), name, mclass, info.get("state", "?")))
    return sorted(rows, key=lambda r: r[1])


def _links(dump: list) -> list[tuple[int, str, str, str]]:
    """(id, output node name, input node name, state) for every link."""
    names = {
        o.get("id"): ((o.get("info", {}) or {}).get("props", {}) or {}).get("node.name", "?")
        for o in dump if o.get("type") == "PipeWire:Interface:Node"
    }
    rows = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info", {}) or {}
        rows.append((
            obj.get("id", -1),
            names.get(info.get("output-node-id"), "?"),
            names.get(info.get("input-node-id"), "?"),
            info.get("state", "?"),
        ))
    return sorted(rows, key=lambda r: (r[2], r[1]))


def capture(label: str) -> None:
    out(f"\n{'=' * 72}")
    out(f"== {label}")
    out(f"{'=' * 72}")

    dump = pw_dump()
    if not dump:
        out("pw-dump returned nothing — is PipeWire running as this user?")
        return

    # Every audio node with its state. The device rows are the answer to
    # "is the headset still being driven"; the effect_* rows say by what.
    out("\n-- audio nodes (id, state, media.class, name) --")
    for nid, name, mclass, state in _node_rows(dump):
        mark = " <-- ASM" if _is_asm_node(name) else ""
        out(f"  {nid:>5}  {state:<10} {mclass:<24} {name}{mark}")

    # Links tell us who is holding the device. A device with no links but a
    # RUNNING state means something other than ASM keeps it awake, which
    # changes the diagnosis entirely.
    out("\n-- links (id, state, source -> destination) --")
    for lid, src, dst, state in _links(dump):
        out(f"  {lid:>5}  {state:<10} {src}  ->  {dst}")

    out("\n-- pw-top (one pass: which nodes actually process audio) --")
    out(run(["pw-top", "-b", "-n", "1"], timeout=20))

    out("\n-- ALSA PCM state (what the kernel thinks) --")
    for pcm in sorted(Path("/proc/asound").glob("card*/pcm*p/sub*/status")):
        try:
            out(f"  {pcm}: {pcm.read_text().strip().splitlines()[0]}")
        except Exception as e:
            out(f"  {pcm}: (unreadable: {e!r})")


def services(action: str) -> None:
    for svc in _SERVICES:
        run(["systemctl", "--user", action, svc], timeout=20)


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture ASM idle-state diagnostics (#180).")
    ap.add_argument("--no-stop", action="store_true",
                    help="skip the ASM-stopped comparison (capture running state only)")
    ap.add_argument("--settle", type=int, default=20,
                    help="seconds to wait after stopping ASM before capturing (default 20)")
    args = ap.parse_args()

    out(f"ASM idle-state capture — {datetime.now().isoformat(timespec='seconds')}")
    out("Make sure NOTHING is playing audio, and leave it that way until this finishes.")

    out("\n-- versions --")
    out(f"  pipewire:    {run(['pipewire', '--version']).splitlines()[0] if shutil.which('pipewire') else '(absent)'}")
    out(f"  wireplumber: {run(['wireplumber', '--version']).splitlines()[0] if shutil.which('wireplumber') else '(absent)'}")
    for svc in _SERVICES:
        state = run(["systemctl", "--user", "is-active", svc])
        out(f"  {svc}: {state}")

    # WirePlumber's own suspend setting decides whether a device may sleep at
    # all. If it is disabled here, nothing ASM does can matter.
    out("\n-- suspend-related config found on this system --")
    found = False
    for d in (Path.home() / ".config/wireplumber", Path("/etc/wireplumber"),
              Path("/usr/share/wireplumber")):
        if not d.is_dir():
            continue
        for f in d.rglob("*.conf"):
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            for line in text.splitlines():
                if "suspend" in line.lower() and not line.strip().startswith("#"):
                    out(f"  {f}: {line.strip()}")
                    found = True
    if not found:
        out("  (nothing — defaults apply, device suspend after ~5s idle)")

    capture("WITH ASM RUNNING (nothing playing)")

    if args.no_stop:
        out("\n(--no-stop given: skipping the comparison capture)")
    else:
        out("\n" + "=" * 72)
        out("The next step STOPS ASM for about "
            f"{args.settle} seconds to capture the comparison, then restarts it.")
        out("Your audio will be interrupted during that time.")
        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            try:
                services("stop")
                # pw-loopback survives the daemon on purpose (node.linger), so
                # leftovers would keep feeding the device and hide the answer.
                run(["pkill", "-f", "pw-loopback"])
                time.sleep(args.settle)
                capture(f"WITH ASM STOPPED ({args.settle}s later, nothing playing)")
            finally:
                out("\nRestarting ASM services...")
                services("start")
                out("Done.")
        else:
            out("Skipped — capture covers the running state only.")

    report = Path.home() / f"asm-idle-state-{datetime.now():%Y%m%d-%H%M%S}.txt"
    try:
        report.write_text("\n".join(_out_lines), encoding="utf-8")
        print(f"\nSaved to: {report}")
        print("Please attach that file to https://github.com/loteran/Arctis-Sound-Manager/issues/180")
    except Exception as e:
        print(f"\nCould not write the report ({e!r}) — copy the output above instead.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Never leave the user's audio stack down because they pressed Ctrl-C.
        print("\nInterrupted — restarting ASM services to be safe.")
        services("start")
        sys.exit(1)
