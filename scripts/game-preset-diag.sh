#!/usr/bin/env bash
# Arctis Sound Manager — why is a game's automatic preset not applied? (#328)
#
# Shows what the automatic preset sees: the audio streams and clients of the
# running game, which one ASM takes for the game, the Steam id it reads from
# it, and the preset that game resolves to. Run it with the game running and
# its sound on the Game channel; it asks you to move the game to Media half
# way, and looks again:
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/develop/scripts/game-preset-diag.sh)
#
# Read-only: nothing is changed. Report: ~/asm-game-preset.txt

set -uo pipefail

snapshot() {
python3 - <<'EOF'
import json, sys
try:
    import pulsectl
    from arctis_sound_manager import clip_capture as cc, game_presets as gp
except Exception as exc:
    print(f"cannot import ASM with {sys.executable}: {exc!r}")
    sys.exit()

def game_ids(pid):
    """(launcher ids found in the environment, under a game runtime?)"""
    env = cc.process_environ_dict(pid)
    found = " ".join(f"{k}={env[k]}" for k in
                     (*gp._STEAM_ENV_KEYS, "GAME_NAME", "HEROIC_APP_NAME") if k in env)
    if not env:
        found = "unreadable"
    hint = any(h in cc._process_cmdline(pid) + cc._process_environ(pid)
               for h in cc._GAME_RUNTIME_HINTS)
    return found, hint

def ids(pid):
    if pid is None:
        return "pid=?"
    found, hint = game_ids(pid)
    return f"pid={pid} env=[{found}] runtime_hint={hint}"

def pid_of(props):
    try:
        return int(props.get("application.process.id", ""))
    except (TypeError, ValueError):
        return None

with pulsectl.Pulse("asm-game-preset-diag") as pulse:
    sinks = {s.index: s.name for s in pulse.sink_list()}
    print("--- playback streams (in the order ASM checks them)")
    for si in pulse.sink_input_list():
        p = si.proplist
        name = p.get("application.name") or p.get("application.process.binary") or ""
        print(f"  #{si.index} sink={sinks.get(si.sink, si.sink)} corked={si.corked} "
              f"audible={cc._is_audible(si)} name={name!r} "
              f"bin={p.get('application.process.binary', '')!r} "
              f"not_a_game={cc._is_not_a_game(name.removesuffix('.exe').strip())} "
              f"{ids(pid_of(p))}")
    print("--- clients run by a game runtime or launcher")
    for c in pulse.client_list():
        pid = pid_of(c.proplist)
        if pid is None:
            continue
        found, hint = game_ids(pid)
        if hint or (found and found != "unreadable"):
            print(f"  client #{c.index} name={c.proplist.get('application.name', '')!r} {ids(pid)}")

print("--- what ASM detects")
print(f"  stream rule:  {cc.detect_game_process(strict=True)}")
print(f"  client rule:  {gp._detect_game_client()}")
game = gp.detect_current_game()
print(f"  game:         {game}")
if game is not None:
    print(f"  association:  {gp.get_association(game.key)}")
    try:
        from arctis_sound_manager.gui.sonar_page import _list_presets
        presets = list(_list_presets("game").keys())
        print(f"  title match:  {gp.match_preset(game.title, presets)!r} "
              f"(among {len(presets)} Game presets)")
        print(f"  target:       {gp.target_preset(game, presets)!r}")
    except Exception as exc:
        print(f"  presets not readable: {exc!r}")
cur = gp.CONFIG_FILE.parent / ".sonar_preset_game"
print(f"  current Game preset: {cur.read_text().strip() if cur.exists() else '(none)'}")
EOF
}

main() {
REPORT="$HOME/asm-game-preset.txt"
exec > >(tee "$REPORT") 2>&1

echo "ASM game preset report — $(date '+%Y-%m-%d %H:%M:%S')"
command -v pacman >/dev/null && pacman -Q arctis-sound-manager 2>/dev/null
echo "=== settings ($HOME/.config/arctis_manager/game_presets.json)"
cat "$HOME/.config/arctis_manager/game_presets.json" 2>/dev/null || echo "(none)"
echo
echo "=== 1. game on the Game channel"
snapshot
echo
echo ">>> Now move the game to the Media channel in ASM, wait until the preset changes, then press Enter."
read -r _ </dev/tty
echo "=== 2. game on the Media channel"
snapshot
echo
echo "Report saved to: $REPORT — please attach it to the GitHub issue."
}

main "$@"
