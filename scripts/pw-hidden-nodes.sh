#!/usr/bin/env bash
# Arctis Sound Manager — which nodes does the system hide from ASM? (#325)
#
# On some SteamOS installs, PipeWire objects (ASM's Sonar EQ nodes among them)
# get their permissions set to "-----" for every client, so ASM cannot link
# to them. This names those objects and finds the WirePlumber rule or script
# that hides them. Run it ON THE HOST, with ASM running and Sonar on:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/develop/scripts/pw-hidden-nodes.sh)
#
# Read-only: nothing is changed. Report: ~/asm-hidden-nodes.txt

set -uo pipefail

main() {

REPORT="$HOME/asm-hidden-nodes.txt"
exec > >(tee "$REPORT") 2>&1

echo "ASM hidden nodes report — $(date '+%Y-%m-%d %H:%M:%S')"
echo "host: $(pw-cli --version 2>&1 | tail -1) / $(wireplumber --version 2>&1 | grep -m1 -i libwireplumber)"
grep -E '^(PRETTY_NAME|VERSION_ID|BUILD_ID)=' /etc/os-release 2>/dev/null
command -v pacman >/dev/null && pacman -Q pipewire wireplumber 2>/dev/null

DUMP="$(mktemp)"
trap 'rm -f "$DUMP"' RETURN
pw-dump >"$DUMP" 2>/dev/null

# Every client's explicit per-object entries (the default line is skipped):
# one "<client> <object> <perms>" line each, for the Python part below.
ENTRIES="$(mktemp)"
trap 'rm -f "$DUMP" "$ENTRIES"' RETURN
for c in $(python3 -c '
import json, sys
for o in json.load(open(sys.argv[1])):
    if o.get("type") == "PipeWire:Interface:Client":
        print(o["id"])
' "$DUMP"); do
    pw-cli -- get-permissions "$c" 2>/dev/null \
        | grep -E '^ *[0-9]+:' | tr -d ' ' | sed "s/^/$c /; s/:/ /"
done >"$ENTRIES"

python3 - "$DUMP" "$ENTRIES" <<'EOF'
import json, sys
from collections import defaultdict
dump = json.load(open(sys.argv[1]))
by_id = {o["id"]: o for o in dump}
props = lambda o: (o.get("info") or {}).get("props") or {}

def describe(oid):
    o = by_id.get(oid)
    if o is None:
        return f"{oid}: (gone)"
    p = props(o)
    kind = o.get("type", "?").rsplit(":", 1)[-1]
    name = (p.get("node.name") or p.get("port.alias") or p.get("object.path")
            or p.get("application.name") or p.get("module.name")
            or p.get("factory.name") or "")
    owner = by_id.get(int(p["client.id"])) if str(p.get("client.id", "")).isdigit() else None
    owner_name = props(owner).get("application.name", "") if owner else ""
    extra = " ".join(f"{k}={p[k]}" for k in ("media.class", "node.group",
                     "node.link-group", "object.serial") if k in p)
    return f"{oid}: {kind} {name!r} {extra} owner={p.get('client.id', '-')} {owner_name!r}"

hidden = defaultdict(set)   # object -> clients that cannot see it
explicit = defaultdict(list)
for line in open(sys.argv[2]):
    client, obj, perms = line.split()
    explicit[int(client)].append(f"{obj}:{perms}")
    if "r" not in perms:
        hidden[int(obj)].add(int(client))

clients = [o for o in dump if o.get("type") == "PipeWire:Interface:Client"]
print(f"\n=== objects hidden from at least one client ({len(hidden)})")
for oid in sorted(hidden):
    print(f"  {describe(oid)}  hidden from {len(hidden[oid])}/{len(clients)} clients")

print("\n=== clients")
for o in clients:
    p = props(o)
    print(f"  {o['id']}: {p.get('application.name', '')!r} "
          f"bin={p.get('application.process.binary', '')} "
          f"access={p.get('pipewire.access', '')}/{p.get('pipewire.access.effective', '')} "
          f"explicit=[{' '.join(explicit.get(o['id'], []))}]")
EOF

echo
echo "=== WirePlumber rules and scripts that hide objects"
for dir in /usr/share/wireplumber /etc/wireplumber "$HOME/.config/wireplumber" \
           /usr/share/pipewire /etc/pipewire "$HOME/.config/pipewire"; do
    [ -d "$dir" ] || continue
    grep -rlE 'hide-parent|software-dsp|update_permissions|PermissionManager|attach_permission_manager' "$dir" 2>/dev/null \
        | while read -r f; do
            owner=""
            command -v pacman >/dev/null && owner="$(pacman -Qoq "$f" 2>/dev/null)"
            echo "--- $f ${owner:+(package: $owner)}"
            if [ "$(wc -l <"$f")" -le 200 ] && [[ "$f" == *.conf ]]; then
                cat "$f"
            else
                grep -nE -B6 -A12 'hide-parent|software-dsp|update_permissions|PermissionManager|attach_permission_manager' "$f"
            fi
        done
done

echo
echo "=== wireplumber log lines about hidden nodes / permissions (this boot)"
journalctl --user -u wireplumber -b --no-pager 2>&1 \
    | grep -iE "dsp|hide|permission|access" | tail -40

echo
echo "Report saved to: $REPORT — please attach it to the GitHub issue."
}

main "$@"
