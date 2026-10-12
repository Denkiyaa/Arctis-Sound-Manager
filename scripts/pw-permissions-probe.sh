#!/usr/bin/env bash
# Arctis Sound Manager — PipeWire link-permission probe (#181, #325)
#
# Answers what the logs cannot:
#   1. Does this system refuse links between FRESH clients (nothing to do with
#      ASM), started on the host and started inside the container?
#   2. Does a numeric grant (0730 = rwxml) make a refused link go through?
#   3. Does the session manager rewrite the permissions afterwards?
#   4. What permissions do ASM's own clients carry right now?
#
# Run it ON THE HOST (Konsole in Desktop Mode), with ASM running and Sonar on:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/develop/scripts/pw-permissions-probe.sh)
#
# It starts two short-lived test loopbacks named asm-probe-*, removes them at
# the end, and only ever changes permissions of clients behind asm-probe-* or
# ASM's own nodes. Report: ~/asm-perm-probe-YYYYMMDD-HHMMSS.txt

set -uo pipefail

# Whole script in main(): `bash <(curl …)` or `curl … | bash` both safe (#181).
main() {

CONTAINER="arctis-sound-manager"
REPORT="$HOME/asm-perm-probe-$(date +%Y%m%d-%H%M%S).txt"
WORK="$(mktemp -d)"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
PIDS=()

cleanup() {
    for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
    if command -v distrobox >/dev/null; then
        distrobox enter "$CONTAINER" -- pkill -f "node.name=asm-probe-" </dev/null >/dev/null 2>&1
    fi
    pkill -f "node.name=asm-probe-" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

exec > >(tee "$REPORT") 2>&1

say() { printf '\n=== %s\n' "$*"; }

# Reads pw-dump on stdin. Commands:
#   probe <prefix>                 -> "out_node in_node out_owner in_owner out_FL in_FL"
#   asm                            -> ASM clients, their access, and unlinked pairs
cat > "$WORK/dump.py" <<'PY'
import json, sys
d = json.load(sys.stdin)
cmd = sys.argv[1]
nodes = {o["id"]: (o.get("info") or {}).get("props") or {} for o in d
         if o.get("type") == "PipeWire:Interface:Node"}
clients = {o["id"]: (o.get("info") or {}).get("props") or {} for o in d
           if o.get("type") == "PipeWire:Interface:Client"}
ports = [o for o in d if o.get("type") == "PipeWire:Interface:Port"]
links = [(o.get("info") or {}) for o in d if o.get("type") == "PipeWire:Interface:Link"]

def node_id(name):
    return next((i for i, p in nodes.items() if p.get("node.name") == name), None)

def port(nid, direction, ch):
    for o in ports:
        pp = o["info"]["props"]
        if (str(pp.get("node.id")) == str(nid) and o["info"].get("direction") == direction
                and pp.get("audio.channel") == ch and not pp.get("port.monitor")):
            return o["id"]
    return None

if cmd == "probe":
    a, b = node_id(sys.argv[2] + "-a-out"), node_id(sys.argv[2] + "-b-in")
    if a is None or b is None:
        print("missing"); sys.exit()
    print(a, b, nodes[a].get("client.id"), nodes[b].get("client.id"),
          port(a, "output", "FL"), port(b, "input", "FL"))
elif cmd == "asm":
    own = ("Arctis_", "effect_input.sonar-", "effect_output.sonar-",
           "effect_input.virtual-surround", "effect_output.virtual-surround")
    seen = set()
    for i, p in sorted(nodes.items()):
        if p.get("node.name", "").startswith(own):
            c = p.get("client.id")
            c = str(c) if c is not None else None
            cp = clients.get(int(c)) if c else {}
            print(f"node {i:>4} {p['node.name']:<45} client {c} "
                  f"access={cp.get('pipewire.access')} app={cp.get('application.name')}")
            if c:
                seen.add(c)
    print("CLIENTS", " ".join(sorted(seen)))
    linked = {(l.get("output-port-id"), l.get("input-port-id")) for l in links}
    for ch in ("game", "chat", "media"):
        cap = ch.capitalize()
        a = node_id(f"Arctis_{cap}_sink_out")
        b = node_id(f"effect_input.sonar-{ch}-eq")
        if a is None or b is None:
            continue
        pa, pb = port(a, "output", "FL"), port(b, "input", "FL")
        state = "linked" if (pa, pb) in linked else "NOT linked"
        print(f"PAIR {ch} {a} {b} {nodes[a].get('client.id')} {nodes[b].get('client.id')} "
              f"{pa} {pb} {state.replace(' ', '_')}")
PY

dump() { pw-dump 2>/dev/null | python3 "$WORK/dump.py" "$@"; }
perms() { pw-cli -- get-permissions "$1" 2>&1 | grep -E "default|^ *($2|$3):" | tr -s ' ' | tr '\n' ' '; }
grant() {  # grant <owner> <object...>
    local o="$1"; shift
    pw-cli -- permissions "$o" -1 0730 >/dev/null 2>&1
    for obj in "$@"; do pw-cli -- permissions "$o" "$obj" 0730 >/dev/null 2>&1; done
}
try_link() {  # try_link <label> <runner...> -- <out> <in>
    local label="$1"; shift
    local out
    out="$("$@" 2>&1)"; local rc=$?
    echo "  link via $label: rc=$rc ${out:-ok}"
}

# One full round on a fresh pair. $1 = where (host|container), $2 = link runner.
probe_pair() {
    local where="$1" prefix="asm-probe-$1"
    local cmd=(pw-loopback
        --capture-props="node.name=${prefix}-a-in media.class=Audio/Sink node.autoconnect=false audio.position=[FL FR]"
        --playback-props="node.name=${prefix}-a-out node.autoconnect=false audio.position=[FL FR]")
    local cmd2=(pw-loopback
        --capture-props="node.name=${prefix}-b-in node.autoconnect=false audio.position=[FL FR]"
        --playback-props="node.name=${prefix}-b-out node.autoconnect=false audio.position=[FL FR]")
    if [ "$where" = host ]; then
        "${cmd[@]}" >/dev/null 2>&1 & PIDS+=($!)
        "${cmd2[@]}" >/dev/null 2>&1 & PIDS+=($!)
    else
        distrobox enter "$CONTAINER" -- "${cmd[@]}" </dev/null >/dev/null 2>&1 & PIDS+=($!)
        distrobox enter "$CONTAINER" -- "${cmd2[@]}" </dev/null >/dev/null 2>&1 & PIDS+=($!)
    fi
    sleep 4
    read -r A B OA OB PA PB <<<"$(dump probe "$prefix")"
    if [ "$A" = missing ] || [ -z "${PB:-}" ]; then
        echo "  test loopbacks did not appear ($where) — skipped"; return
    fi
    echo "  nodes out=$A (client $OA) in=$B (client $OB), ports $PA -> $PB"
    echo "  client $OA access: $(pw-cli -- info "$OA" 2>/dev/null | grep -m1 'pipewire.access' | tr -s ' ')"
    echo "  perms before: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
    try_link "host pw-link" pw-link "$PA" "$PB"
    pw-link -d "$PA" "$PB" >/dev/null 2>&1
    if command -v distrobox >/dev/null; then
        try_link "container pw-link" distrobox enter "$CONTAINER" -- pw-link "$PA" "$PB" </dev/null
        pw-link -d "$PA" "$PB" >/dev/null 2>&1
    fi
    echo "  -> granting 0730 to both owners"
    grant "$OA" "$A" "$B" "$PA" "$PB"; grant "$OB" "$A" "$B" "$PA" "$PB"
    echo "  perms after grant: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
    try_link "host pw-link after grant" pw-link "$PA" "$PB"
    pw-link -d "$PA" "$PB" >/dev/null 2>&1
    if command -v distrobox >/dev/null; then
        try_link "container pw-link after grant" distrobox enter "$CONTAINER" -- pw-link "$PA" "$PB" </dev/null
        pw-link -d "$PA" "$PB" >/dev/null 2>&1
    fi
    sleep 10
    echo "  perms 10 s later: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
}

echo "ASM PipeWire permission probe — $STARTED_AT"
say "versions"
echo "host:      $(pw-cli --version 2>&1 | tail -1) / $(wireplumber --version 2>&1 | grep -m1 -i 'libwireplumber' || echo 'wireplumber ?')"
if command -v distrobox >/dev/null; then
    echo "container: $(distrobox enter "$CONTAINER" -- pw-cli --version </dev/null 2>&1 | tail -1)"
    echo "ASM:       $(distrobox enter "$CONTAINER" -- asm-cli --version </dev/null 2>&1 | tail -1)"
fi
grep -E '^(VERSION_ID|BUILD_ID)=' /etc/os-release

say "1. fresh test pair started on the HOST"
probe_pair host
say "2. fresh test pair started in the CONTAINER"
if command -v distrobox >/dev/null; then probe_pair container; else echo "  no distrobox — skipped"; fi

say "3. ASM's own clients right now"
dump asm > "$WORK/asm.txt"
grep -v '^CLIENTS\|^PAIR' "$WORK/asm.txt"
for c in $(grep '^CLIENTS' "$WORK/asm.txt" | cut -d' ' -f2-); do
    echo "  client $c: $(pw-cli -- get-permissions "$c" 2>&1 | grep -E 'default' | tr -s ' ')" \
         "explicit entries: $(pw-cli -- get-permissions "$c" 2>&1 | grep -cE '^ *[0-9]+:')"
done

say "4. one unlinked Sonar hop: numeric grant, then link from the host"
PAIR="$(grep '^PAIR' "$WORK/asm.txt" | grep 'NOT_linked' | head -1)"
if [ -z "$PAIR" ]; then
    echo "  every Sonar hop is linked right now (is Sonar on?)"
    grep '^PAIR' "$WORK/asm.txt"
else
    read -r _ CH A B OA OB PA PB _ <<<"$PAIR"
    echo "  $CH: node $A (client $OA) -> node $B (client $OB), FL ports $PA -> $PB"
    echo "  perms before: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
    try_link "host pw-link" pw-link "$PA" "$PB"
    grant "$OA" "$A" "$B" "$PA" "$PB"; grant "$OB" "$A" "$B" "$PA" "$PB"
    echo "  perms after grant: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
    try_link "host pw-link after grant" pw-link "$PA" "$PB"
    sleep 10
    echo "  perms 10 s later: [$OA] $(perms "$OA" "$B" "$PB")| [$OB] $(perms "$OB" "$A" "$PA")"
fi

say "ASM log lines during the probe (its own grants may interfere)"
journalctl --user -u arctis-manager --since "$STARTED_AT" --no-pager 2>/dev/null \
    | grep -E "granted link permission|did not take effect|Operation not permitted" | tail -8

echo
echo "Report saved to: $REPORT — please attach it to the GitHub issue."
}

main "$@"
