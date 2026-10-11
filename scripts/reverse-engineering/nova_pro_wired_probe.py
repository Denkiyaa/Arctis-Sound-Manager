#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Arctis Nova Pro Wired GameDAC probe for issue #321 (1038:12cb / 12cd / 12fa).

Symptom this exists for: with ASM running, the DAC ends up with the volume
maxed, the sound cut and the mic dead until it is replugged, and the wheel
lags. Three suspects, each tested on its own so the answer is attributable:

  * ASM used to poll opcode 0xB0 and send 0xBF at init. SteelSeries' own spec
    for this family (base_arctis_nova_pro) defines neither; an unknown opcode
    on SET_REPORT is expected to STALL (EPIPE) and may upset the firmware.
  * ASM reads the wheel events with a 0.1 s pause after every read; the DAC
    may send faster than that and the lag is the queue draining slowly.
  * ASM drew the OLED as 1024-byte frames over 64 rows; GG draws 128x52 at
    y=12 as 521-byte HID FEATURE chunks and gives the screen back with 0x95.

Phases (each is announced and waits for Enter):

  A  read-only baseline: ask 0x20 (audio_settings) and decode the reply
  B  command acceptance: 0x20, 0x10, then the two suspects 0xB0 and 0xBF
  C  wheel timing: 15 s continuous drain, then 15 s with ASM's 0.1 s sleep
  D  wheel right after 0xB0 sent five times (2 s apart), 10 s continuous
  E  OLED (optional): GG-style draw, then reload_display 0x95

Nothing is sent that changes a setting, except 0xBF (mic LED brightness,
value 0x0A = the profile's 100 % default) and the optional OLED test image.
EQ, gain, firmware and ChatMix commands are never sent.

Run with the daemon stopped (it owns the interface):

    systemctl --user stop arctis-manager
    python3 nova_pro_wired_probe.py
    systemctl --user start arctis-manager

Paste the final block (also saved to ~/asm-321-probe.txt) into the issue.
Replug the DAC afterwards: phases B and D may leave it in the odd state.
"""
from __future__ import annotations

import errno
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter

try:
    import usb.core
    import usb.util
except ImportError:  # the pure helpers (and the tests) work without pyusb
    usb = None  # type: ignore[assignment]

VENDOR = 0x1038
PRODUCTS = {0x12CB: "Nova Pro Wired", 0x12CD: "Nova Pro Wired (Xbox)",
            0x12FA: "Nova Pro Wired V2"}
INTERFACE = 4
DEFAULT_IN_ENDPOINT = 0x88
REPORT_ID = 0x06
COMMAND_SIZE = 16
COMMAND_SPACING_S = 0.050          # GG's time-between-commands
WVALUE_OUTPUT = 0x0206             # output report, id 6
WVALUE_FEATURE = 0x0306            # feature report, id 6
FEATURE_CHUNK = 521
OLED_X, OLED_Y, OLED_W, OLED_H = 0, 12, 128, 52
OUTPUT_FILE = os.path.expanduser("~/asm-321-probe.txt")

WHEEL_PREFIX = (0x07, 0x25)        # wheel event: [07 25 volume ...], volume 0-56
BURST_WINDOW_S = 0.050


# ---------------------------------------------------------------- pure helpers

def hexs(data) -> str:
    return " ".join(f"{b:02x}" for b in data)


def pad(data, size: int = COMMAND_SIZE) -> list[int]:
    return (list(data) + [0] * size)[:size]


def align8(x: int) -> int:
    return ((x - 1) // 8 + 1) * 8


def decode_audio_settings(frame) -> dict:
    """Decode the reply to [06 20] (spec: audio_settings). None-safe."""
    f = list(frame)
    if len(f) < 8 or f[0] != REPORT_ID or f[1] != 0x20:
        return {}
    inputs = {0: "USB-1", 1: "USB-2", 2: "XXX", 3: "Xbox"}
    out = {
        "audio_input": f"{f[2]} ({inputs.get(f[2], '?')})",
        "volume_0_56": f[3],
        "gain": f"{f[4]} ({'high' if f[4] == 2 else 'low' if f[4] == 1 else '?'})",
        "surround": f[5],
        "eq_preset": f[6],
    }
    if len(f) >= 16:
        out["custom_eq_bands_1_9"] = " ".join(str(b) for b in f[7:16])
    return out


def is_wheel(frame) -> bool:
    return len(frame) >= 3 and (frame[0], frame[1]) == WHEEL_PREFIX


def summarize_events(frames) -> dict:
    """frames: list of (monotonic_seconds, bytes). Wheel-event statistics."""
    frames = sorted(frames, key=lambda t: t[0])
    wheel = [(t, f) for t, f in frames if is_wheel(f)]
    others = Counter(hexs(f[:2]) for _, f in frames if not is_wheel(f))
    times = [t for t, _ in wheel]
    gaps = [(b - a) * 1000.0 for a, b in zip(times, times[1:])]
    burst, lo = 0, 0
    for hi, t in enumerate(times):           # max events inside any 50 ms window
        while t - times[lo] > BURST_WINDOW_S:
            lo += 1
        burst = max(burst, hi - lo + 1)
    return {
        "frames_total": len(frames),
        "wheel_events": len(wheel),
        "other_frames": dict(others),
        "min_gap_ms": round(min(gaps), 1) if gaps else None,
        "median_gap_ms": round(statistics.median(gaps), 1) if gaps else None,
        "max_gap_ms": round(max(gaps), 1) if gaps else None,
        "max_burst_50ms": burst,
        "first_volume": wheel[0][1][2] if wheel else None,
        "last_volume": wheel[-1][1][2] if wheel else None,
    }


def make_test_image() -> list[list[bool]]:
    """128x52: border, filled block top-left (shows orientation), '321' centre."""
    digits = {
        "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
        "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
        "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    }
    img = [[False] * OLED_W for _ in range(OLED_H)]
    for x in range(OLED_W):
        img[0][x] = img[OLED_H - 1][x] = True
    for y in range(OLED_H):
        img[y][0] = img[y][OLED_W - 1] = True
    for y in range(3, 11):
        for x in range(3, 15):
            img[y][x] = True
    scale, gap = 4, 4
    left = (OLED_W - (3 * 5 * scale + 2 * gap)) // 2
    top = (OLED_H - 7 * scale) // 2
    for n, ch in enumerate("321"):
        for r, line in enumerate(digits[ch]):
            for c, cell in enumerate(line):
                if cell == "#":
                    for dy in range(scale):
                        for dx in range(scale):
                            img[top + r * scale + dy][left + n * (5 * scale + gap)
                                                      + c * scale + dx] = True
    return img


def pack_columns(image: list[list[bool]], msb_top: bool = False) -> bytes:
    """Column-packed bitmap: per column, align8(h)/8 bytes, bit = row % 8.

    The spec splits the data by width halves (s1 = w1*hp/8 bytes), which only
    works if the data is column-major - so that is what this produces.
    """
    h, w = len(image), len(image[0])
    hp = align8(h)
    out = bytearray()
    for x in range(w):
        for page in range(hp // 8):
            byte = 0
            for bit in range(8):
                y = page * 8 + bit
                if y < h and image[y][x]:
                    byte |= (0x80 >> bit) if msb_top else (1 << bit)
            out.append(byte)
    return bytes(out)


def build_draw_bitmap_chunks(x: int, y: int, w: int, h: int, data: bytes,
                             size: int = FEATURE_CHUNK) -> list[list[int]]:
    """GG's draw_bitmap (0x93): 1 chunk if data <= 512 bytes, else two halves
    of the width. Each chunk is [06 93 x y w hp data...] zero-padded to size."""
    hp = align8(h)
    total = w * hp // 8
    if len(data) != total:
        raise ValueError(f"expected {total} data bytes for {w}x{h}, got {len(data)}")
    head = [REPORT_ID, 0x93]
    if total <= 512:
        return [pad(head + [x, y, w, hp] + list(data), size)]
    w1 = w // 2
    w2 = w - w1
    s1 = w1 * hp // 8
    s2 = w2 * hp // 8
    return [
        pad(head + [x, y, w1, hp] + list(data[:s1]), size),
        pad(head + [x + w1, y, w2, hp] + list(data[s1:s1 + s2]), size),
    ]


# ------------------------------------------------------------------ USB layer

class Probe:
    def __init__(self, dev, ep_in: int):
        self.dev = dev
        self.ep_in = ep_in
        self.last_send = 0.0
        self.lines: list[str] = []
        self.lock = threading.Lock()

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def rec(self, text: str) -> None:
        """Print and keep for the final summary."""
        self.say(text)
        self.lines.append(text)

    def write(self, wvalue: int, payload) -> str:
        """SET_REPORT; returns 'OK' or 'ERR errno=N (text)'. Honours the 50 ms spacing."""
        with self.lock:
            wait = COMMAND_SPACING_S - (time.monotonic() - self.last_send)
            if wait > 0:
                time.sleep(wait)
            try:
                self.dev.ctrl_transfer(0x21, 0x09, wvalue, INTERFACE, list(payload),
                                       timeout=1000)
                result = "OK"
            except usb.core.USBError as exc:
                note = " STALL" if exc.errno == errno.EPIPE else ""
                result = f"ERR errno={exc.errno}{note} ({exc})"
            self.last_send = time.monotonic()
        return result

    def command(self, cmd) -> str:
        return self.write(WVALUE_OUTPUT, pad(cmd))

    def read_frames(self, seconds: float, sleep_after_read: float = 0.0):
        """Read the interrupt IN endpoint for `seconds`; list of (t, bytes)."""
        frames = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            remaining_ms = max(1, int((end - time.monotonic()) * 1000))
            try:
                data = self.dev.read(self.ep_in, 16, timeout=min(remaining_ms, 100))
                frames.append((time.monotonic(), bytes(data)))
            except usb.core.USBError as exc:
                if exc.errno not in (errno.ETIMEDOUT, 110, None):
                    frames.append((time.monotonic(), b""))
                    self.rec(f"   read error: {exc}")
                    time.sleep(0.05)
            if sleep_after_read:
                time.sleep(sleep_after_read)
        return frames

    def drain(self) -> None:
        self.read_frames(0.1)

    def query(self, cmd, window: float = 0.3):
        """Send cmd, return (result, frames received within `window`)."""
        self.drain()
        result = self.command(cmd)
        return result, self.read_frames(window)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def yes_no(prompt: str) -> str:
    while True:
        a = ask(prompt + " [y/n] ").lower()
        if a in ("y", "n"):
            return a
        if a == "":
            return "(no answer)"


def daemon_running() -> str | None:
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "arctis-manager"],
                           capture_output=True, text=True, timeout=5)
        if r.stdout.strip() == "active":
            return "systemd user unit arctis-manager is active"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        r = subprocess.run(["pgrep", "-f", "asm-daemon|arctis_sound_manager.*daemon"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return f"a daemon process is running (pid {r.stdout.split()[0]})"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def versions() -> list[str]:
    out = [f"python {platform.python_version()} on {platform.platform()}"]
    out.append(f"pyusb {getattr(usb, '__version__', '?')}")
    try:
        from importlib.metadata import version
        out.append(f"arctis-sound-manager {version('arctis-sound-manager')}")
    except Exception:
        out.append("arctis-sound-manager: not importable")
    return out


# --------------------------------------------------------------------- phases

def phase_a(p: Probe, res: dict) -> None:
    result, frames = p.query([REPORT_ID, 0x20], 0.5)
    p.rec(f"[A] send 06 20: {result}")
    reply = next((f for _, f in frames if f[:2] == bytes([REPORT_ID, 0x20])), None)
    for _, f in frames:
        p.rec(f"[A]   rx {hexs(f)}")
    if reply is None:
        p.rec("[A] NO audio_settings reply - the DAC is not answering.")
        res["A"] = "no reply"
        return
    dec = decode_audio_settings(reply)
    for k, v in dec.items():
        p.rec(f"[A]   {k}: {v}")
    res["A"] = f"reply ok, volume={dec.get('volume_0_56')}"


def phase_b(p: Probe, res: dict) -> None:
    tests = [
        ("0x20 audio_settings (spec)", [REPORT_ID, 0x20]),
        ("0x10 fw_versions (spec: dsp/mcu/usb versions)", [REPORT_ID, 0x10]),
        ("0xB0 status poll (NOT in spec, ASM used to send)", [REPORT_ID, 0xB0]),
        ("0xBF mic LED brightness=10 (NOT in spec, ASM used to send)",
         [REPORT_ID, 0xBF, 0x0A]),
    ]
    out = []
    for name, cmd in tests:
        result, frames = p.query(cmd)
        rx = "; ".join(hexs(f) for _, f in frames if not is_wheel(f)) or "no reply"
        p.rec(f"[B] {name}: tx {hexs(pad(cmd))} -> {result} | rx: {rx}")
        out.append(f"{hexs(cmd)}: {result.split(' ')[0]}"
                   + (f" errno" + result.split("errno=")[1].split(" ")[0]
                      if "errno=" in result else ""))
    result, frames = p.query([REPORT_ID, 0x20], 0.5)
    again = any(f[:2] == bytes([REPORT_ID, 0x20]) for _, f in frames)
    p.rec(f"[B] re-query 0x20 after the suspects: {result}, "
          f"{'DAC still answers' if again else 'NO REPLY - DAC stopped answering'}")
    res["B"] = "; ".join(out) + f"; 0x20 after suspects: {'answers' if again else 'SILENT'}"


def summary_text(s: dict) -> str:
    return (f"wheel_events={s['wheel_events']} min_gap={s['min_gap_ms']}ms "
            f"median_gap={s['median_gap_ms']}ms max_gap={s['max_gap_ms']}ms "
            f"max_burst_in_50ms={s['max_burst_50ms']} "
            f"volume {s['first_volume']}->{s['last_volume']} other={s['other_frames']}")


def log_frames(p: Probe, tag: str, frames) -> None:
    if not frames:
        return
    t0 = frames[0][0]
    for t, f in frames:
        p.lines.append(f"[{tag}] +{t - t0:8.3f}s {hexs(f)}")


def phase_c(p: Probe, res: dict) -> None:
    p.say("C1: 15 s, draining as fast as possible. Turn the knob SLOWLY for ~7 s, "
          "then FAST for ~7 s. Press Enter to start.")
    ask("")
    p.drain()
    frames = p.read_frames(15)
    log_frames(p, "C1", frames)
    s = summarize_events(frames)
    p.rec(f"[C1] {summary_text(s)}")
    res["C1_stats"] = summary_text(s)
    res["C1_smooth"] = yes_no("Did the volume follow the knob smoothly?")
    p.rec(f"[C1] smooth: {res['C1_smooth']}")

    p.say("\nC2: same again, but reading with a 0.1 s pause after each read (what ASM "
          "does). Slow then fast again. Press Enter to start.")
    ask("")
    p.drain()
    frames = p.read_frames(15, sleep_after_read=0.1)
    log_frames(p, "C2", frames)
    s = summarize_events(frames)
    p.rec(f"[C2] {summary_text(s)}")
    res["C2_stats"] = summary_text(s)
    res["C2_smooth"] = yes_no("Did the volume follow the knob smoothly?")
    p.rec(f"[C2] smooth: {res['C2_smooth']}")


def phase_d(p: Probe, res: dict) -> None:
    p.say("D: a background thread sends 06 B0 five times (2 s apart) while we drain "
          "for 10 s. Turn the knob throughout. Press Enter to start.")
    ask("")
    errors: list[str] = []

    def spam() -> None:
        for i in range(5):
            r = p.command([REPORT_ID, 0xB0])
            errors.append(f"#{i + 1}: {r}")
            time.sleep(2)

    p.drain()
    th = threading.Thread(target=spam, daemon=True)
    th.start()
    frames = p.read_frames(10)
    th.join(timeout=5)
    log_frames(p, "D", frames)
    s = summarize_events(frames)
    for e in errors:
        p.rec(f"[D] 0xB0 {e}")
    p.rec(f"[D] {summary_text(s)}")
    res["D_sends"] = "; ".join(errors)
    res["D_stats"] = summary_text(s)
    res["D_misbehaved"] = yes_no("Did the volume misbehave (jump, max out, lag, "
                                 "sound cut)?")
    p.rec(f"[D] misbehaved: {res['D_misbehaved']}")


def phase_e(p: Probe, res: dict, state: dict) -> None:
    a = yes_no("E: OLED test (draws a test image on the DAC screen, then gives it "
               "back). Run it?")
    if a != "y":
        res["E"] = "skipped"
        return
    chunks = build_draw_bitmap_chunks(OLED_X, OLED_Y, OLED_W, OLED_H,
                                      pack_columns(make_test_image()))
    state["drawn"] = True
    for i, c in enumerate(chunks):
        r = p.write(WVALUE_FEATURE, c)
        p.rec(f"[E] chunk {i + 1}/{len(chunks)} ({len(c)} bytes, "
              f"hdr {hexs(c[:6])}): {r}")
    p.drain()
    p.say("Look at the DAC screen.")
    res["E_screen"] = ask("What does it show (border + '321' under y=12? garbage? "
                          "nothing? unchanged?) > ") or "(no answer)"
    res["E_knob"] = yes_no("Does the knob still work (volume changes)?")
    r = p.command([REPORT_ID, 0x95])
    state["drawn"] = False
    p.rec(f"[E] reload_display 06 95: {r}")
    time.sleep(0.5)
    res["E_reload"] = yes_no("Is the original DAC screen back?")
    p.rec(f"[E] screen: {res['E_screen']} | knob: {res['E_knob']} | "
          f"reload back: {res['E_reload']}")


# ----------------------------------------------------------------------- main

def announce(letter: str, title: str, text: str) -> None:
    print(f"\n{'=' * 70}\nPHASE {letter} - {title}\n{text}\n{'=' * 70}")
    ask("Press Enter to continue (Ctrl-C to stop and print the summary)... ")


def find_ep_in(dev) -> int:
    try:
        cfg = dev.get_active_configuration()
        intf = usb.util.find_descriptor(cfg, bInterfaceNumber=INTERFACE)
        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                return ep.bEndpointAddress
    except Exception:
        pass
    return DEFAULT_IN_ENDPOINT


def write_summary(header: list[str], res: dict, p: Probe | None, note: str) -> None:
    out = ["===== ASM #321 probe summary =====", *header, note]
    for k, v in res.items():
        out.append(f"{k}: {v}")
    if p is not None:
        out.append("--- log ---")
        out.extend(p.lines)
    out += ["===== end =====", "",
            "Now replug the DAC, then: systemctl --user start arctis-manager"]
    text = "\n".join(out)
    print("\n" + text)
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\n(saved to {OUTPUT_FILE})")
    except OSError as exc:
        print(f"(could not write {OUTPUT_FILE}: {exc})")


def main() -> int:
    if usb is None:
        print("pyusb is missing. Install python-pyusb (or: pip install --user pyusb).")
        return 1
    running = daemon_running()
    if running:
        print(f"Refusing to run: {running}.\n"
              "Stop it first:  systemctl --user stop arctis-manager")
        return 1

    dev = usb.core.find(custom_match=lambda d: d.idVendor == VENDOR
                        and d.idProduct in PRODUCTS)
    if dev is None:
        print("No Arctis Nova Pro Wired found (1038:12cb / 12cd / 12fa). "
              "Is the DAC plugged in by USB?")
        return 1
    header = [f"device: {VENDOR:04x}:{dev.idProduct:04x} {PRODUCTS[dev.idProduct]}",
              *versions()]
    print("\n".join(header))

    res: dict = {}
    state = {"drawn": False}
    p: Probe | None = None
    detached = claimed = False
    note = "completed"
    try:
        try:
            if dev.is_kernel_driver_active(INTERFACE):
                dev.detach_kernel_driver(INTERFACE)
                detached = True
            usb.util.claim_interface(dev, INTERFACE)
            claimed = True
        except usb.core.USBError as exc:
            if exc.errno == errno.EACCES:
                print("Permission denied. The udev rules shipped with ASM are not "
                      "applied: run `asm-setup`, replug the DAC, retry.")
            elif exc.errno == errno.EBUSY:
                print("Interface 4 is busy: something else still holds it "
                      "(is arctis-manager really stopped?).")
            else:
                print(f"Cannot claim interface {INTERFACE}: {exc}")
            return 1
        p = Probe(dev, find_ep_in(dev))
        print(f"interface {INTERFACE} claimed, IN endpoint {p.ep_in:#04x}")
        for letter, title, text, fn in [
            ("A", "baseline (read-only)",
             "Asks 06 20 (audio_settings) and decodes the reply.", phase_a),
            ("B", "command acceptance",
             "Sends 06 20, 06 10 (read-only) then the two commands the spec does "
             "not define:\n06 B0 and 06 BF 0A, once each, 50 ms apart.", phase_b),
            ("C", "wheel timing",
             "Two 15 s listening runs; you turn the knob slow then fast.", phase_c),
            ("D", "wheel after 06 B0 x5",
             "Sends the unknown 06 B0 five times and watches the wheel.", phase_d),
        ]:
            announce(letter, title, text)
            fn(p, res)
        announce("E", "OLED (optional)",
                 "GG-style draw_bitmap (521-byte feature chunks, 128x52 at y=12), "
                 "then reload_display.")
        phase_e(p, res, state)
    except KeyboardInterrupt:
        note = "INTERRUPTED by Ctrl-C (partial results)"
        print("\nInterrupted.")
    except usb.core.USBError as exc:
        note = f"STOPPED by USB error: {exc}"
        print(f"\nUSB error: {exc}")
    finally:
        if p is not None and state["drawn"]:
            try:
                p.command([REPORT_ID, 0x95])     # never leave our image on the DAC
            except Exception:
                pass
        if claimed:
            try:
                usb.util.release_interface(dev, INTERFACE)
            except Exception:
                pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        if detached:
            try:
                dev.attach_kernel_driver(INTERFACE)
            except Exception as exc:
                print(f"(could not re-attach the kernel driver: {exc} - replug the DAC)")
        write_summary(header, res, p, note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
