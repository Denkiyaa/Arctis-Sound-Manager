#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Show why PipeWire refuses to link ASM's nodes together.

Symptom this exists for (issue #181): the channels have no sound, and both
ASM and the user get the same refusal when connecting a channel to its EQ:

    pw-link "Arctis_Game_sink_out:output_FL" "effect_input.sonar-game-eq:playback_FL"
    failed to link ports: Operation not permitted

That message does not come from file permissions or from running as the wrong
user. PipeWire refuses a link when the *client that owns* one of the two nodes
cannot see the other node: a check between the two owning clients, not between
the user and the nodes. Which is why `pw-link -o` and `-i` list both ports
perfectly while the link is denied.

Answering it needs two things the ordinary bug report does not carry yet: the
owning client of each node, and what access each of those clients was granted.
This prints both, plus the link table, so the whole picture arrives in one
paste. Both are being added to `asm-cli diagnose`; this script exists so the
question can be answered before that ships.

Read-only. It runs pw-dump, prints, and changes nothing.

Usage:
    python3 link_permissions.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

# Nodes worth reporting on: ASM's own, plus any real output device.
_INTERESTING = ("Arctis_", "sonar-", "virtual-surround", "effect_input", "effect_output")
_DEVICES = ("alsa_output.", "alsa_input.", "bluez_output.")


def pw_dump() -> list:
    if shutil.which("pw-dump") is None:
        sys.exit("pw-dump not found — install pipewire-utils (or pipewire-tools).")
    try:
        raw = subprocess.run(["pw-dump"], capture_output=True, text=True,
                             timeout=30).stdout
        data = json.loads(raw)
    except Exception as exc:
        sys.exit(f"could not read pw-dump: {exc!r}")
    if not isinstance(data, list):
        sys.exit("pw-dump returned something unexpected")
    return data


def main() -> int:
    dump = pw_dump()

    clients: dict[str, dict] = {}
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Client":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        clients[str(obj.get("id"))] = props

    print("== nodes and the client that owns each ==")
    print("(a node with no owner is exempt from the permission check entirely)\n")
    owners: set[str] = set()
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        name = props.get("node.name", "?")
        if not (any(f in name for f in _INTERESTING) or name.startswith(_DEVICES)):
            continue
        owner = props.get("client.id")
        if owner is not None:
            owners.add(str(owner))
        print(f"  {obj.get('id'):>5}  {info.get('state', '?'):<10} "
              f"owner={str(owner) if owner is not None else '(none, daemon-owned)':<22} "
              f"{name}")

    print("\n== what those owning clients were granted ==")
    print("(pipewire.sec.* set = the client is behind a security context)\n")
    for cid in sorted(owners, key=lambda x: int(x) if x.isdigit() else 0):
        props = clients.get(cid)
        if props is None:
            print(f"  {cid:>5}  (client no longer present)")
            continue
        sec = " ".join(f"{k}={v}" for k, v in props.items()
                       if k.startswith("pipewire.sec."))
        print(f"  {cid:>5}  access={props.get('pipewire.access', '(unset)'):<14} "
              f"{props.get('application.process.binary') or props.get('application.name', '?')}"
              + (f"  [{sec}]" if sec else ""))

    print("\n== links between ASM nodes ==")
    names = {o.get("id"): ((o.get("info") or {}).get("props") or {}).get("node.name", "?")
             for o in dump if o.get("type") == "PipeWire:Interface:Node"}
    found = False
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        src = names.get(info.get("output-node-id"), "?")
        dst = names.get(info.get("input-node-id"), "?")
        if any(f in src or f in dst for f in _INTERESTING):
            found = True
            print(f"  {info.get('state', '?'):<10} {src}  ->  {dst}")
    if not found:
        print("  (none — this is the failure: the channels reach nothing)")

    print("\n== next step ==")
    print("If the owners above have different access levels, or any shows a")
    print("pipewire.sec.* field, that alone explains the refusal. To confirm it")
    print("is the ownership check, grant both owners full permissions and retry")
    print("the link. This is runtime-only and undone by restarting PipeWire:\n")
    for cid in sorted(owners, key=lambda x: int(x) if x.isdigit() else 0):
        print(f"    pw-cli permissions {cid} -1 rwxml")
    print('    pw-link "Arctis_Game_sink_out:output_FL" '
          '"effect_input.sonar-game-eq:playback_FL"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
