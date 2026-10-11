#!/usr/bin/env bash
# Arctis Sound Manager - OpenRC diagnostic script (issue #320)
#
# ASM supports systemd and dinit. To add OpenRC we need to know exactly how
# your machine starts PipeWire, what the session environment looks like and
# what breaks when the ASM daemon runs without systemd. This script collects
# all of it in one report.
#
# Usage (plug your headset in first, run it as your normal user, NOT root):
#   bash scripts/openrc-diag.sh              # full run, incl. a 25 s live test
#   bash scripts/openrc-diag.sh --no-live    # skip the live daemon test and every prompt
#   bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/develop/scripts/openrc-diag.sh)
#
# Output: ~/asm-openrc-diag.txt
# It never uses sudo, never installs/enables anything, only writes to $HOME
# (the report file) and reads system state. The installer is only run with
# --dry-run; anything that really installs/configures asks y/N first (default N).

set -u

# Everything lives in main(), invoked on the last line, so `curl | bash`
# cannot have a command swallow the rest of the script from stdin.
main() {
    LIVE=1
    for a in "$@"; do
        case "$a" in
            --no-live) LIVE=0 ;;
            -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null; return 0 ;;
        esac
    done

    REPORT="$HOME/asm-openrc-diag.txt"
    TMPOUT="$HOME/.asm-openrc-out.$$"
    trap 'rm -f "$TMPOUT"' EXIT
    : > "$REPORT" 2>/dev/null || { echo "Cannot write $REPORT" >&2; return 1; }

    if [ "$(id -u)" = "0" ]; then
        echo "Please run this as your normal user (not root)." >&2
        return 1
    fi

    # ask: y/N prompt on the terminal, default No; No when no tty (curl|bash safe).
    ask() {
        ( : < /dev/tty ) 2>/dev/null || return 1
        printf '%s' "$1" > /dev/tty
        ans=""
        read -r ans < /dev/tty || ans=""
        case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
    }
    have() { command -v "$1" >/dev/null 2>&1; }
    out() { printf '%s\n' "$*" >> "$REPORT"; }
    section() { printf '\n\n=================== %s ===================\n' "$1" >> "$REPORT"; echo "  - $1"; }

    # run: print the command, then its (time-limited, size-capped) output, then the rc.
    cap() { head -n 400 "$1" | cut -c1-400 >> "$REPORT"; }
    run() {
        printf '\n$ %s\n' "$*" >> "$REPORT"
        if have timeout; then
            timeout 15 "$@" > "$TMPOUT" 2>&1 </dev/null
        else
            "$@" > "$TMPOUT" 2>&1 </dev/null
        fi
        rc=$?
        cap "$TMPOUT"
        printf '[exit %s]\n' "$rc" >> "$REPORT"
    }
    # runsh: same for a shell pipeline string.
    runsh() {
        printf '\n$ %s\n' "$1" >> "$REPORT"
        if have timeout; then
            timeout 20 sh -c "$1" > "$TMPOUT" 2>&1 </dev/null
        else
            sh -c "$1" > "$TMPOUT" 2>&1 </dev/null
        fi
        rc=$?
        cap "$TMPOUT"
        printf '[exit %s]\n' "$rc" >> "$REPORT"
    }
    # showfile: cat a file (size-limited) if it exists.
    showfile() {
        if [ -e "$1" ]; then
            printf '\n--- %s ---\n' "$1" >> "$REPORT"
            head -n "${2:-80}" "$1" >> "$REPORT" 2>&1
        else
            printf '\n--- %s: (absent)\n' "$1" >> "$REPORT"
        fi
    }

    echo "Arctis Sound Manager - OpenRC diagnostic (issue #320)"
    echo "Collecting information (read-only) ..."

    out "ASM OpenRC diagnostic report"
    out "Generated: $(date 2>/dev/null)"
    out "Script live-test: $([ "$LIVE" = 1 ] && echo enabled || echo disabled)"
    out "User: $(id -un 2>/dev/null) (uid $(id -u))   Shell: ${SHELL:-?}   Home: $HOME"

    # ------------------------------------------------------------------
    section "1. System"
    showfile /etc/os-release 30
    run uname -a
    runsh 'cat /proc/1/comm; echo "--- /proc/1/exe:"; readlink /proc/1/exe; echo "--- /proc/1/cmdline:"; tr "\0" " " < /proc/1/cmdline; echo'
    runsh 'ls -l /sbin/init /usr/sbin/init /bin/init 2>&1'
    runsh 'command -v systemctl dinitctl rc-service rc-status rc-update openrc openrc-run supervise-daemon s6-svc sv runsv runit loginctl elogind-inhibit seatd turnstiled 2>&1'
    runsh 'python3 --version 2>&1; python --version 2>&1'

    # ------------------------------------------------------------------
    section "2. OpenRC: version and user-service support (needs OpenRC >= 0.60)"
    run openrc --version
    run rc-status --version
    runsh 'rc-service --help 2>&1 | head -30'
    runsh 'rc-update --help 2>&1 | head -30'
    runsh 'openrc --help 2>&1 | head -30'
    run rc-status --runlevel
    run rc-status --user
    run rc-status --user --all
    run rc-service --user --list
    run rc-update --user show
    runsh 'ls -la /etc/user /etc/user/init.d /etc/user/conf.d /etc/user/rc.conf 2>&1 | head -60'
    runsh 'ls -laR ~/.config/rc ~/.config/openrc 2>&1 | head -80'
    runsh 'ls -la /usr/lib/openrc /usr/libexec/rc /lib/rc 2>&1 | head -30'
    showfile /etc/rc.conf 200
    showfile /etc/conf.d/pipewire 40
    showfile /etc/conf.d/elogind 40
    runsh 'ls /etc/runlevels/*/ 2>&1'
    runsh 'ls /etc/init.d 2>&1 | head -150'
    runsh 'grep -rIl -i "pipewire\|wireplumber\|pulseaudio" /etc/init.d /etc/user/init.d /etc/conf.d 2>/dev/null'

    # ------------------------------------------------------------------
    section "3. Supervision / session managers (supervise-daemon, s6, runit, elogind, seatd, turnstile)"
    runsh 'ps -eo pid,ppid,user,args 2>/dev/null | grep -i -E "supervise-daemon|s6-|runsv|runsvdir|elogind|seatd|turnstile|dbus-daemon|dbus-broker|polkitd|udevd|eudev|mdev" | grep -v grep'
    run loginctl list-sessions
    run loginctl show-user "$(id -un)"
    runsh 'loginctl session-status 2>&1 | head -30'
    runsh 'ls -la /run/user /run/turnstile /run/elogind* 2>&1 | head -30'
    showfile /etc/turnstile/turnstiled.conf 60
    runsh 'ls -la /etc/turnstile /etc/dinit.d /etc/s6 /etc/sv /etc/runit 2>&1 | head -40'

    # ------------------------------------------------------------------
    section "4. Session environment (variables ASM needs; others are NOT printed)"
    for v in XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS WAYLAND_DISPLAY DISPLAY XDG_SESSION_TYPE \
             XDG_SESSION_ID XDG_SESSION_CLASS XDG_SEAT XDG_VTNR XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP \
             XDG_CONFIG_HOME XDG_DATA_HOME XDG_DATA_DIRS XDG_CONFIG_DIRS PIPEWIRE_RUNTIME_DIR \
             PULSE_SERVER PULSE_RUNTIME_PATH PATH XAUTHORITY DESKTOP_SESSION LANG; do
        out "$v=${!v-<unset>}"
    done
    runsh 'ls -la "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" 2>&1 | head -50'
    runsh 'id'
    runsh 'ls -l /dev/snd 2>&1 | head -20'
    runsh 'ls -ld /dev/bus/usb 2>&1; groups'

    # ------------------------------------------------------------------
    section "5. D-Bus session bus"
    if have busctl; then run busctl --user list --no-pager; fi
    if have dbus-send; then
        run dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
    fi
    if have gdbus; then run gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.ListNames; fi
    runsh 'command -v dbus-daemon dbus-launch dbus-run-session dbus-broker-launch 2>&1'
    runsh 'ps -eo pid,ppid,user,args 2>/dev/null | grep -E "dbus" | grep -v grep'

    # ------------------------------------------------------------------
    section "6. PipeWire stack: who starts what (process tree)"
    run ps -eo pid,ppid,user,args
    runsh 'ps -eo pid,ppid,user,args 2>/dev/null | grep -i -E "pipewire|wireplumber|pulseaudio|pw-loopback|filter-chain|gentoo-pipewire|pipewire-launcher|asm-|arctis" | grep -v grep'
    runsh 'for p in $(pgrep -u "$(id -u)" -x "pipewire|pipewire-pulse|wireplumber|pw-loopback" 2>/dev/null || pgrep pipewire 2>/dev/null); do echo "== pid $p"; tr "\0" " " < /proc/$p/cmdline; echo; echo "parent chain:"; q=$p; i=0; while [ "$q" -gt 1 ] 2>/dev/null && [ $i -lt 8 ]; do q=$(awk "/^PPid:/{print \$2}" /proc/$q/status 2>/dev/null); [ -n "$q" ] && { printf "  <- %s " "$q"; tr "\0" " " < /proc/$q/cmdline; echo; }; i=$((i+1)); done; done'
    runsh 'ls -la ~/.config/autostart /etc/xdg/autostart 2>&1 | grep -i -E "pipewire|wireplumber|pulse|arctis|^total|autostart" '
    runsh 'for f in /etc/xdg/autostart/*pipewire* /etc/xdg/autostart/*wireplumber* /etc/xdg/autostart/*pulse* ~/.config/autostart/*pipewire* ~/.config/autostart/*wireplumber*; do [ -e "$f" ] && { echo "--- $f"; cat "$f"; }; done'
    runsh 'command -v gentoo-pipewire-launcher pipewire pipewire-pulse wireplumber pw-cli pw-link pw-dump pw-loopback pactl wpctl 2>&1'
    runsh 'for f in $(command -v gentoo-pipewire-launcher) /etc/pipewire/pipewire.conf.d/* ; do [ -f "$f" ] && { echo "--- $f"; head -n 60 "$f"; }; done'
    runsh 'ls -laR ~/.config/pipewire ~/.config/wireplumber /etc/pipewire /usr/share/pipewire 2>&1 | head -120'
    runsh 'ls ~/.xinitrc ~/.xprofile ~/.xsession ~/.profile ~/.bash_profile ~/.zprofile ~/.config/sway/config ~/.config/hypr/hyprland.conf ~/.config/i3/config ~/.config/river/init 2>/dev/null'
    runsh 'grep -n -i -E "pipewire|wireplumber|pulse|dbus-|asm|arctis" ~/.xinitrc ~/.xprofile ~/.xsession ~/.profile ~/.bash_profile ~/.zprofile ~/.config/sway/config ~/.config/hypr/hyprland.conf ~/.config/i3/config ~/.config/river/init 2>/dev/null'

    section "7. PipeWire: version, socket, filter-chain"
    run pipewire --version
    run wireplumber --version
    run pipewire-pulse --version
    run pw-cli --version
    run pactl info
    run pw-cli info 0
    runsh 'ls -la "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/pipewire-* "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/pulse 2>&1'
    runsh 'ps -eo pid,ppid,args 2>/dev/null | grep -E "pipewire.* -c|filter-chain" | grep -v grep || echo "(no filter-chain process)"'
    runsh 'ls -la /usr/share/pipewire/filter-chain.conf /etc/pipewire/filter-chain.conf ~/.config/pipewire/filter-chain.conf ~/.config/pipewire/filter-chain.conf.d 2>&1'
    runsh 'ls -la /usr/share/pipewire/filter-chain /usr/share/pipewire/pipewire.conf 2>&1 | head -30'
    run pw-cli ls Node
    runsh 'pw-link -o 2>&1 | head -60; echo ---; pw-link -i 2>&1 | head -60'
    runsh 'pactl list short sinks 2>&1; pactl list short sources 2>&1'

    # ------------------------------------------------------------------
    section "8. ASM install state"
    runsh 'command -v asm-daemon asm-gui asm-setup asm-cli asm-router asm-stream-guard arctis-manager 2>&1'
    runsh 'for py in python3 python; do command -v $py >/dev/null 2>&1 && { $py - <<PY
import importlib.metadata as m
try:
    print("$py: arctis-sound-manager", m.version("arctis-sound-manager"))
except Exception as e:
    print("$py: not importable via metadata:", e)
try:
    import arctis_sound_manager as a; print("path:", a.__file__)
except Exception as e:
    print("$py: import failed:", e)
PY
}; done'
    runsh 'pipx list 2>&1 | grep -i -A8 arctis'
    runsh 'pip show arctis-sound-manager 2>&1 | head -8; pip3 show arctis-sound-manager 2>&1 | head -8'
    runsh 'pacman -Qi arctis-sound-manager 2>&1 | head -5; pacman -Qi arctis-sound-manager-git 2>&1 | head -5'
    runsh 'qlist -Iv arctis 2>&1 | head; equery list "*arctis*" 2>&1 | head -5; eix -I arctis 2>&1 | head -5; eix arctis 2>&1 | head -8'
    runsh 'apk info -e arctis-sound-manager 2>&1 | head; apk list -I 2>/dev/null | grep -i arctis'
    runsh 'dpkg -l 2>/dev/null | grep -i arctis; rpm -qa 2>/dev/null | grep -i arctis; xbps-query -l 2>/dev/null | grep -i arctis'
    runsh 'ls -la ~/.local/bin 2>&1 | grep -i -E "asm|arctis"'
    runsh 'ls -la ~/.config/systemd/user/arctis* ~/.config/systemd/user/app-Arctis* ~/.config/dinit.d/* ~/.config/autostart/arctis* ~/.config/rc ~/.config/arctis_manager 2>&1 | head -60'
    runsh 'ls -la /usr/lib/systemd/user/arctis* /etc/xdg/autostart/*rctis* /usr/share/applications/*rctis* /usr/share/arctis-sound-manager 2>&1 | head -30'
    runsh 'ls -la /usr/lib/udev/rules.d/*steelseries* /usr/lib/udev/rules.d/*arctis* /lib/udev/rules.d/*steelseries* /lib/udev/rules.d/*arctis* /etc/udev/rules.d/*steelseries* /etc/udev/rules.d/*arctis* /etc/udev/rules.d/*50-arctis* 2>&1'
    runsh 'for f in /usr/lib/udev/rules.d/*arctis* /usr/lib/udev/rules.d/*steelseries* /etc/udev/rules.d/*arctis* /etc/udev/rules.d/*steelseries* /lib/udev/rules.d/*arctis*; do [ -f "$f" ] && { echo "--- $f"; head -n 25 "$f"; }; done'

    section "9. USB device, udev flavour"
    if have lsusb; then
        run lsusb
        runsh 'lsusb 2>/dev/null | grep -i -E "1038|steelseries"'
    else
        out "(lsusb not installed)"
    fi
    runsh 'for d in /sys/bus/usb/devices/*; do [ -r "$d/idVendor" ] && [ "$(cat $d/idVendor)" = "1038" ] && echo "$d vendor=1038 product=$(cat $d/idProduct) name=$(cat $d/product 2>/dev/null) driver-bound-ifaces:" && ls "$d":* 2>/dev/null | head -0; done'
    runsh 'for d in /sys/bus/usb/devices/*:*; do p=$(dirname "$d"); [ "$(cat $(dirname $d)/idVendor 2>/dev/null)" = "1038" ] && echo "$d driver=$(basename "$(readlink $d/driver 2>/dev/null)")"; done'
    runsh 'ls -la /dev/hidraw* 2>&1 | head -20'
    runsh 'ps -eo pid,args 2>/dev/null | grep -E "udevd|systemd-udevd|eudev|mdev|vdev" | grep -v grep'
    run udevadm --version
    run udevadm info --version
    runsh 'cat /proc/sys/kernel/hotplug 2>&1'
    runsh 'ls -la /etc/udev /etc/mdev.conf 2>&1 | head'
    runsh 'getfacl /dev/hidraw0 2>&1 | head; ls -la /dev/hidraw* 2>&1 | head'

    # ------------------------------------------------------------------
    section "9b. Package managers available (which install paths exist here)"
    runsh 'for c in pacman paru yay emerge eix equery layman eselect apk xbps-install apt dnf zypper nix pip pip3 pipx uv git curl wget gpg tar python3 distrobox; do p=$(command -v $c 2>/dev/null) && echo "$c: $p" || echo "$c: -"; done'
    runsh 'grep -n -E "^\[" /etc/pacman.conf 2>/dev/null | head -30'
    runsh 'cat /etc/portage/repos.conf 2>/dev/null | head -30; ls /etc/portage/repos.conf /var/db/repos 2>&1 | head -20'
    runsh 'cat /etc/apk/repositories 2>/dev/null | head'
    runsh 'ls /etc/xbps.d 2>&1 | head; echo "Void uses runit (not OpenRC) if /etc/runit exists:"; ls -d /etc/runit /var/service /etc/sv 2>&1'
    runsh 'pipx --version 2>&1; uv --version 2>&1; python3 -c "import sys;print(sys.version)" 2>&1; python3 -c "import PySide6" 2>&1 | tail -1; python3 -c "import usb" 2>&1 | tail -1'
    runsh 'command -v ladspa-swh-plugins; ls /usr/lib/ladspa /usr/lib64/ladspa 2>&1 | head -5'

    # ------------------------------------------------------------------
    section "9c. Install attempt: what the official installer does on this machine"
    out "Systemd-only steps in the installer chain (for reference):"
    out "  docs/install.sh (the curl|bash one): detect() only knows Arch/Fedora/Debian/immutable families;"
    out "     Gentoo/Alpine/Void -> 'Couldn't recognise this distribution' (die). Artix (ID_LIKE arch) goes the pacman path."
    out "     post_setup() runs 'asm-setup' ('udev rules, PipeWire, systemd services')."
    out "  asm-setup (scripts/setup.py): _refuse_root; writes ~/.config/pipewire confs; udev rules via 'asm-cli udev write-rules';"
    out "     writes ~/.config/systemd/user/{filter-chain,app-ArctisManager}.service; then (init=systemd)"
    out "     systemctl --user daemon-reload / restart pipewire pipewire-pulse / enable --now arctis-manager,"
    out "     arctis-video-router, arctis-stream-guard, filter-chain. detect_init(): only systemd|dinit, else 'unknown' -> nothing started."
    out "  scripts/install.sh (source/pipx install): systemctl --user enable --now arctis-manager.service,"
    out "     arctis-video-router.service, filter-chain.service, restart pipewire pipewire-pulse."
    TMPD=$(mktemp -d "$HOME/.asm-openrc-inst.XXXXXX" 2>/dev/null || echo "")
    if [ -n "$TMPD" ] && have curl; then
        if curl -fsSL --max-time 30 https://loteran.github.io/Arctis-Sound-Manager/install.sh -o "$TMPD/install.sh" 2>>"$REPORT"; then
            out "Downloaded installer: $(wc -l < "$TMPD/install.sh") lines"
            run bash -n "$TMPD/install.sh"
            runsh "grep -n -i -E 'systemctl|systemd|dinit|openrc|rc-service' '$TMPD/install.sh'"
            out ""
            out "DRY RUN of the installer (bash -x, --dry-run: nothing is installed, only echoed):"
            runsh "bash -x '$TMPD/install.sh' --dry-run --yes"
            if [ "$LIVE" = 1 ] && have asm-setup; then
                out ""
                out "asm-setup is installed; it has no --help/--dry-run (running it WOULD do setup)."
                if ask "Run 'asm-setup' now ? It writes files under \$HOME and may ask for your password (udev rules) [y/N] "; then
                    runsh "asm-setup"
                else
                    out "asm-setup run declined / non-interactive: skipped."
                fi
            fi
            if [ "$LIVE" = 1 ] && ! have asm-setup; then
                if ask "Also try the REAL installer (it installs a package and uses sudo itself; expected to fail on Gentoo/Alpine/Void) [y/N] "; then
                    runsh "bash -x '$TMPD/install.sh' --yes"
                else
                    out "Real installer run declined / non-interactive: skipped."
                fi
            fi
        else
            out "Could not download the installer (network?)."
        fi
        rm -rf "$TMPD"
    else
        out "Skipped (no curl or cannot create temp dir)."
    fi

    # ------------------------------------------------------------------
    section "10. Is an ASM daemon already running?"
    runsh 'ps -eo pid,ppid,user,args 2>/dev/null | grep -E "asm-daemon|arctis_sound_manager|asm-gui|asm-router|asm-stream-guard" | grep -v grep'
    runsh 'tail -n 40 ~/.local/state/arctis_manager/*.log ~/.local/share/arctis_manager/*.log /tmp/arctis-manager.log 2>&1 | head -80'

    # ------------------------------------------------------------------
    section "11. LIVE test: asm-daemon in foreground for ~25 s (no systemd)"
    live_test

    # ------------------------------------------------------------------
    section "12. Summary"
    out "init(/proc/1/comm)  : $(cat /proc/1/comm 2>/dev/null)"
    out "openrc              : $(have openrc && openrc --version 2>&1 | head -1 || echo not-installed)"
    out "openrc --user ok?   : $(openrc --user --help >/dev/null 2>&1 && echo yes || echo no-or-unknown)"
    out "rc-service --user   : $(rc-service --user --list >/dev/null 2>&1 && echo works || echo fails)"
    out "pipewire            : $(pipewire --version 2>&1 | tail -1)"
    out "dbus session bus    : ${DBUS_SESSION_BUS_ADDRESS:-<unset>}"
    out "XDG_RUNTIME_DIR     : ${XDG_RUNTIME_DIR:-<unset>}"
    out "asm-daemon          : $(command -v asm-daemon || echo missing)"
    out "headset (1038)      : $(lsusb 2>/dev/null | grep -i 1038 | head -1 || true)"

    echo
    echo "Report written to ~/asm-openrc-diag.txt — attach it to issue #320"
}

live_test() {
    if [ "$LIVE" != 1 ]; then
        out "Skipped (--no-live)."
        return 0
    fi
    if ! have asm-daemon; then
        out "Skipped: asm-daemon not found in PATH (install ASM first, then re-run to get this section)."
        return 0
    fi
    existing=$(ps -eo pid,args 2>/dev/null | grep -E "asm-daemon|arctis_sound_manager.*daemon" | grep -v grep | grep -v openrc-diag || true)
    if [ -n "$existing" ]; then
        out "Skipped: an ASM daemon is already running (not touching it):"
        out "$existing"
        return 0
    fi

    echo "    LIVE TEST: running asm-daemon in the foreground for ~25 s (then stopped)."
    out "Running 'asm-daemon' in the foreground for 25 s, output captured below."
    out "It will be stopped afterwards, together with any pw-loopback it spawned."

    before=$(pgrep -x pw-loopback 2>/dev/null | sort | tr '\n' ' ')
    out "pw-loopback PIDs before: ${before:-<none>}"

    live_log="$HOME/.asm-openrc-live.$$"
    if have setsid; then
        setsid asm-daemon > "$live_log" 2>&1 < /dev/null &
    else
        asm-daemon > "$live_log" 2>&1 < /dev/null &
    fi
    dpid=$!
    i=0
    while [ $i -lt 25 ] && kill -0 "$dpid" 2>/dev/null; do sleep 1; i=$((i+1)); done
    if kill -0 "$dpid" 2>/dev/null; then
        out "Daemon still alive after ${i}s (expected): stopping it."
        kill -TERM -- "-$dpid" 2>/dev/null || kill -TERM "$dpid" 2>/dev/null
        sleep 3
        kill -0 "$dpid" 2>/dev/null && { kill -KILL -- "-$dpid" 2>/dev/null || kill -KILL "$dpid" 2>/dev/null; }
    else
        wait "$dpid" 2>/dev/null
        out "Daemon EXITED by itself after ${i}s with status $? (this is likely the failure we need to see)."
    fi

    # Snapshot useful state while it was (maybe) up is gone; kill leftovers.
    after=$(pgrep -x pw-loopback 2>/dev/null | sort | tr '\n' ' ')
    for p in $after; do
        case " $before " in *" $p "*) ;; *) kill -TERM "$p" 2>/dev/null; out "killed new pw-loopback $p" ;; esac
    done
    sleep 1
    for p in $after; do
        case " $before " in *" $p "*) ;; *) kill -KILL "$p" 2>/dev/null ;; esac
    done
    out "pw-loopback PIDs after cleanup: $(pgrep -x pw-loopback 2>/dev/null | sort | tr '\n' ' ')"

    printf '\n--- asm-daemon output (stdout+stderr) ---\n' >> "$REPORT"
    head -c 60000 "$live_log" >> "$REPORT" 2>/dev/null
    rm -f "$live_log"
    printf '\n--- end of daemon output ---\n' >> "$REPORT"
    runsh 'ps -eo pid,args 2>/dev/null | grep -E "asm-daemon|pw-loopback" | grep -v grep || echo "(no asm-daemon / pw-loopback left)"'
}

main "$@"
