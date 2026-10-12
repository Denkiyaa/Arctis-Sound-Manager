"""Bring a Bluetooth output back when it drops out from under a channel.

A channel routed to earbuds loses them when the Bluetooth transport fails —
which it does on its own, without the earbuds going anywhere: the log shows
``spa.bluez5: Failure in Bluetooth audio transport`` followed by the adapter
resetting itself, after which BlueZ sits there paired and *not connected* until
someone reconnects by hand. Until then the channel falls back to the headset,
which may well be switched off, and the user hears nothing or a broken stream.

This asks BlueZ to reconnect. It is not clever: the device is one the user
saved as a channel output, so they want it; it is paired, so it is allowed;
and it is gone from the graph while BlueZ says it is not connected, so a
``connect`` is the only thing left to try. Earbuds put back in their case fail
that connect (``Host is down``), so attempts back off — 15 s doubling to five
minutes — and stop costing anything much. The backoff resets the moment the
node is seen again.

It also has to know when to stop. BlueZ cannot say whether a device went away
because the transport failed or because the user pressed Disconnect, and the
first version of this treated both the same — so the earbuds came back within
seconds of being switched off on purpose, every time, "out of spite". The
difference is visible from here, though: a transport failure happens once,
and after the reconnect the device stays. A device that drops out again soon
after this brought it back was sent away by someone, and is left alone until
it is seen on the graph again — that is, until the user reconnects it.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time

_log = logging.getLogger(__name__)

# bluez_output.30_96_10_49_54_E2.1 → 30:96:10:49:54:E2
_BLUEZ_NODE = re.compile(r"^bluez_output\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})\.")

BACKOFF_FIRST_S = 15.0
BACKOFF_MAX_S = 300.0
CONNECT_TIMEOUT_S = 20.0

# A device that vanishes again this soon after a reconnect of ours was not lost
# — it was disconnected. Long enough to cover the user reaching for the applet
# after noticing the earbuds are back; short enough that a transport that fails
# again an hour later still gets reconnected.
DELIBERATE_WINDOW_S = 600.0

# A link that keeps dropping and coming back is not healthy when it comes back.
# Seen here: the transport failed four times in six minutes, BlueZ re-acquired
# it each time, and the stream that resumed was broken — the controller's send
# queue full (182 EAGAIN from media-sink in 15 s), the encoder cutting its
# bitpool 27 times, the earbuds back at 44.1 kHz instead of 48. It sounded
# like the audio was riding over bumps, and it stayed that way. A disconnect
# and a fresh connect cleared all of it: 0 and 0, back at 48 kHz.
#
# So after FLAKY_DROPS drop-outs inside FLAKY_WINDOW_S, once the node has been
# back for STABLE_BEFORE_REFRESH_S, the connection is rebuilt from scratch —
# at most once per REFRESH_COOLDOWN_S, because a link that is failing for a
# reason this cannot fix must not be cut every few minutes on top of it.
FLAKY_DROPS = 3
FLAKY_WINDOW_S = 600.0
STABLE_BEFORE_REFRESH_S = 10.0
REFRESH_COOLDOWN_S = 1800.0
# The first connect straight after a disconnect can be refused
# (br-connection-unknown) and succeed a few seconds later.
REFRESH_CONNECT_TRIES = 4
REFRESH_RETRY_GAP_S = 3.0


def mac_of(node_name: str) -> str | None:
    """The device address a bluez_output node name is built from, or None."""
    m = _BLUEZ_NODE.match(node_name or "")
    return m.group(1).replace("_", ":").upper() if m else None


def device_state(mac: str) -> tuple[bool, bool]:
    """(paired, connected) as BlueZ reports them; (False, False) when unsure."""
    try:
        out = subprocess.run(
            ["bluetoothctl", "info", mac], capture_output=True, text=True,
            timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False, False
    paired = re.search(r"^\s*Paired:\s*yes", out, re.M) is not None
    connected = re.search(r"^\s*Connected:\s*yes", out, re.M) is not None
    return paired, connected


def refresh_connection(mac: str) -> bool:
    """Disconnect *mac* and connect it again from scratch. True when it is back.

    The same thing as switching Bluetooth off and on for one device, which is
    what clears a link that resumed broken (see FLAKY_DROPS) — and what nobody
    would think to try without knowing that is what happened.
    """
    try:
        subprocess.run(["bluetoothctl", "disconnect", mac], capture_output=True,
                       text=True, timeout=10, check=False)
        for attempt in range(REFRESH_CONNECT_TRIES):
            time.sleep(REFRESH_RETRY_GAP_S)
            res = subprocess.run(["bluetoothctl", "connect", mac], capture_output=True,
                                 text=True, timeout=CONNECT_TIMEOUT_S, check=False)
            if "Connection successful" in res.stdout:
                return True
            _log.debug("bluetooth: reconnect of %s, try %d: %s", mac, attempt + 1,
                       (res.stdout + res.stderr).strip()[-120:])
    except (OSError, subprocess.SubprocessError) as exc:
        _log.debug("bluetooth: refresh of %s failed: %s", mac, exc)
    return False


class BluetoothReconnector:
    """Call :meth:`tick` from the watchdog with the saved outputs and a
    ``node_exists`` predicate; it reconnects what is missing, with backoff."""

    def __init__(self) -> None:
        self._next_try: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        # mac → when this reconnected it, while the node has not shown up yet
        self._reconnected_at: dict[str, float] = {}
        # mac → when this reconnected it, once the node was seen back
        self._returned_at: dict[str, float] = {}
        # Devices the user sent away after a reconnect; nothing until they return.
        self._dismissed: set[str] = set()
        # For FLAKY_DROPS: whether the node was on the graph last tick, since
        # when, when it dropped out lately, and when it was last rebuilt.
        self._present: dict[str, bool] = {}
        self._present_since: dict[str, float] = {}
        self._drops: dict[str, list[float]] = {}
        self._refreshed_at: dict[str, float] = {}
        # Being rebuilt right now: its absence is ours, not a drop-out.
        self._refreshing: set[str] = set()

    def tick(self, saved_outputs: dict[str, str], node_exists, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for node in set(saved_outputs.values()):
            mac = mac_of(node)
            if mac is None:
                continue
            with self._lock:
                if mac in self._refreshing:
                    continue
            present = node_exists(node)
            was_present = self._present.get(mac)
            self._present[mac] = present
            if present and was_present is not True:
                self._present_since[mac] = now
            if not present and was_present:
                self._drops.setdefault(mac, []).append(now)
            if present:
                if mac in self._delay or mac in self._dismissed:
                    _log.info("bluetooth: %s is back on the graph", node)
                self._delay.pop(mac, None)
                self._next_try.pop(mac, None)
                self._dismissed.discard(mac)
                with self._lock:
                    when = self._reconnected_at.pop(mac, None)
                if when is not None:
                    self._returned_at[mac] = when
                self._maybe_refresh(mac, node, now)
                continue
            if mac in self._dismissed:
                continue
            since = self._returned_at.pop(mac, None)
            if since is not None and now - since < DELIBERATE_WINDOW_S:
                _log.info("bluetooth: %s dropped out %.0fs after being reconnected "
                          "— taking that as deliberate, leaving it alone until "
                          "it comes back on its own", node, now - since)
                self._dismissed.add(mac)
                continue
            if now < self._next_try.get(mac, 0.0):
                continue
            with self._lock:
                if mac in self._busy:
                    continue
                self._busy.add(mac)
            delay = self._delay.get(mac, BACKOFF_FIRST_S)
            self._delay[mac] = min(delay * 2, BACKOFF_MAX_S)
            self._next_try[mac] = now + delay
            threading.Thread(target=self._connect, args=(mac, node, delay, now),
                             name=f"bt-reconnect-{mac}", daemon=True).start()

    def _maybe_refresh(self, mac: str, node: str, now: float) -> None:
        """Rebuild a connection that keeps dropping, once it is back and settled."""
        drops = [t for t in self._drops.get(mac, []) if now - t <= FLAKY_WINDOW_S]
        self._drops[mac] = drops
        if len(drops) < FLAKY_DROPS:
            return
        if now - self._present_since.get(mac, now) < STABLE_BEFORE_REFRESH_S:
            return
        last = self._refreshed_at.get(mac)
        if last is not None and now - last < REFRESH_COOLDOWN_S:
            return
        with self._lock:
            if mac in self._busy or mac in self._refreshing:
                return
            self._refreshing.add(mac)
        self._refreshed_at[mac] = now
        self._drops[mac] = []
        _log.info("bluetooth: %s dropped out %d times in %.0f min — reconnecting it "
                  "from scratch, since a link that keeps coming back tends to come "
                  "back broken", node, len(drops), FLAKY_WINDOW_S / 60)
        threading.Thread(target=self._refresh, args=(mac, node),
                         name=f"bt-refresh-{mac}", daemon=True).start()

    def _refresh(self, mac: str, node: str) -> None:
        try:
            ok = refresh_connection(mac)
            _log.info("bluetooth: %s %s", node,
                      "reconnected from scratch" if ok else
                      "did not come back after the rebuild — the usual reconnect takes over")
        finally:
            with self._lock:
                self._refreshing.discard(mac)
            # Its absence during the rebuild was ours: not a drop-out, and not
            # the user sending it away after a reconnect of ours either.
            self._present.pop(mac, None)
            self._returned_at.pop(mac, None)

    def _connect(self, mac: str, node: str, delay: float, now: float) -> None:
        try:
            paired, connected = device_state(mac)
            if not paired or connected:
                # Not ours to connect, or already connected and the node simply
                # has not appeared yet (profile still switching) — wait.
                return
            _log.info("bluetooth: %s is paired but not connected — asking BlueZ "
                      "to reconnect (channel output %s)", mac, node)
            res = subprocess.run(
                ["bluetoothctl", "connect", mac], capture_output=True, text=True,
                timeout=CONNECT_TIMEOUT_S, check=False)
            if "Connection successful" in res.stdout:
                _log.info("bluetooth: %s reconnected", mac)
                with self._lock:
                    self._reconnected_at[mac] = now
            else:
                tail = (res.stdout + res.stderr).strip().splitlines()[-1:]
                _log.info("bluetooth: %s did not reconnect (%s) — next try in %.0fs",
                          mac, tail[0] if tail else "no answer", delay)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.debug("bluetooth: reconnect of %s failed: %s", mac, exc)
        finally:
            with self._lock:
                self._busy.discard(mac)
