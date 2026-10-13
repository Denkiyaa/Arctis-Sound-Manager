#!/usr/bin/env bash
# Arctis Sound Manager — why does a link permission not stick? (#325)
#
# Grants the Chat loopback's client access to the Sonar Chat EQ node, reads
# the permission back, and saves what PipeWire and WirePlumber logged about
# it. Run it ON THE HOST, with ASM running and Sonar on:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/develop/scripts/pw-permission-log.sh)
#
# Only that one client's permission on that one node is touched — the same
# grant ASM itself makes. Report: ~/asm-perm-log.txt

set -uo pipefail

main() {

REPORT="$HOME/asm-perm-log.txt"
exec > >(tee "$REPORT") 2>&1

ids="$(pw-dump 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
props = lambda o: (o.get("info") or {}).get("props") or {}
def node(name):
    return next((o for o in d if o.get("type") == "PipeWire:Interface:Node"
                 and props(o).get("node.name") == name), None)
out, eq = node("Arctis_Chat_sink_out"), node("effect_input.sonar-chat-eq")
if out is None or eq is None:
    print("missing")
else:
    print(props(out).get("client.id"), eq["id"], props(eq).get("client.id"))
')"

echo "ASM permission log — $(date '+%Y-%m-%d %H:%M:%S')"
echo "host: $(pw-cli --version 2>&1 | tail -1) / $(wireplumber --version 2>&1 | grep -m1 -i libwireplumber)"
if [ "$ids" = "missing" ] || [ -z "$ids" ]; then
    echo "Arctis_Chat_sink_out or effect_input.sonar-chat-eq not found — is ASM running with Sonar on?"
    return
fi
read -r CLIENT NODE EQ_CLIENT <<<"$ids"
echo "Chat loopback client $CLIENT, Sonar Chat EQ node $NODE (owned by client $EQ_CLIENT)"

entry() { pw-cli -- get-permissions "$CLIENT" 2>&1 | grep -E "^ *(default|$NODE):" | tr -s ' ' | tr '\n' ' '; }
echo "before: $(entry)"
# PipeWire logs permission changes at info level, below its usual one:
# raise it for the few seconds of the grant, then put the old value back.
OLD_LEVEL="$(pw-metadata -n settings 0 log.level 2>/dev/null | grep -o "value:'[0-9]*'" | grep -o '[0-9]*')"
pw-metadata -n settings 0 log.level 4 >/dev/null 2>&1
STARTED="$(date '+%Y-%m-%d %H:%M:%S')"
sleep 1
echo "pw-cli: $(pw-cli -- permissions "$CLIENT" "$NODE" 0730 2>&1 | tr '\n' ' ')"
echo "right after: $(entry)"
sleep 3
echo "3 s later:   $(entry)"
pw-metadata -n settings 0 log.level "${OLD_LEVEL:-2}" >/dev/null 2>&1

echo
echo "=== pipewire / wireplumber log since the grant"
journalctl --user -u pipewire -u wireplumber --since "$STARTED" --no-pager -o short-precise 2>&1 \
    | grep -iE "permission|invalid global|global $NODE|client $CLIENT|access|error|warn" | tail -60
echo
echo "=== pipewire / wireplumber log lines mentioning permissions (this boot)"
journalctl --user -u pipewire -u wireplumber -b --no-pager 2>&1 \
    | grep -iE "permission|invalid global|access" | tail -30
echo
echo "Report saved to: $REPORT — please attach it to the GitHub issue."
}

main "$@"
