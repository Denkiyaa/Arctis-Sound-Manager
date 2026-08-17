#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find out why only the left half of a Nova Pro DAC screen updates.

Symptom this exists for: on a wired Nova Pro GameDAC, ASM's custom display
shows its content on the left half of the panel while the right half keeps
whatever the DAC firmware last drew there. ggoled shows the same thing on the
same hardware — `ggoled text "hello"` prints "he" — so this is not specific to
either program, and that is what makes it worth probing rather than patching.

Why the split is at exactly half the screen: the panel is 128 px wide and the
frame is sent as strips of at most 64 px, one HID SET_REPORT each. The first
strip lands, the second appears not to. Two explanations fit that equally well
from the outside, and they need opposite fixes:

  TIMING  the second report arrives too soon after the first and the firmware
          drops it -> the fix is a delay between packets.
  OFFSET  the second report is accepted but its x origin (64) is interpreted
          differently than we assume -> the fix is in how the header is built,
          and a delay would change nothing.

Each test below draws a filled block and tells you where it should appear.
Comparing what you actually see separates the two:

  Test 2 (right strip alone, nothing before it) is the decisive one.
    - block on the RIGHT half  -> the strip itself is fine, the problem is
                                  sending two in a row: TIMING.
    - nothing, or a block on
      the LEFT half            -> x=64 is not landing where we think: OFFSET.

Run it with the ASM daemon stopped, so nothing else is drawing to the panel:

    systemctl --user stop arctis-manager
    python3 oled_strip_probe.py
    systemctl --user start arctis-manager

Needs pyusb and permission to talk to the DAC (the udev rules ASM installs are
enough). It only draws to the screen — no setting is written, and the DAC's own
UI comes back at the end.
"""
from __future__ import annotations

import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing: pip install --user pyusb")

VENDOR = 0x1038
# Every Nova Pro DAC/base station known to carry this panel. The wired ones are
# what this probe is about; the wireless ones are here so it can be run on a
# working device to see what a correct result looks like.
PRODUCTS = {
    0x12cb: "Arctis Nova Pro Wired",
    0x12cd: "Arctis Nova Pro Wired (Xbox)",
    0x12fa: "Arctis Nova Pro Wired (variant)",
    0x12e0: "Arctis Nova Pro Wireless",
    0x12e5: "Arctis Nova Pro Wireless (Xbox)",
}

INTERFACE = 4
REPORT_ID = 0x06
CMD_SCREEN = 0x93
CMD_RETURN_UI = 0x95
REPORT_SIZE = 1024
CONTROL_REPORT_SIZE = 64
WIDTH, HEIGHT = 128, 64

_FEATURE, _OUTPUT = 0x03, 0x02


def _packet(header: list[int], body: list[int], size: int = REPORT_SIZE) -> list[int]:
    payload = header + body
    payload.extend([0] * max(0, size - len(payload)))
    return payload[:size]


def _filled_strip(x: int, width: int, height: int = HEIGHT) -> list[int]:
    """A solid block `width` px wide at column `x` — every pixel lit.

    The panel takes columns of 8 vertical pixels per byte ("pages"), so a full
    block is simply 0xff repeated for width * height/8 bytes.
    """
    return [0xFF] * (width * (height // 8))


def send(dev, packet: list[int], control: bool = False) -> bool:
    report_type = _OUTPUT if control else _FEATURE
    wvalue = (report_type << 8) | REPORT_ID
    bm = usb.util.build_request_type(
        direction=usb.util.CTRL_OUT,
        type=usb.util.CTRL_TYPE_CLASS,
        recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
    )
    try:
        dev.ctrl_transfer(bm, 0x09, wvalue, INTERFACE, packet)
        return True
    except usb.core.USBError as e:
        print(f"      !! USB error: {e}")
        return False


def strip(dev, x: int, width: int) -> bool:
    """Draw one solid strip at x, exactly as ASM builds it."""
    header = [REPORT_ID, CMD_SCREEN, x, 0, width, HEIGHT]
    return send(dev, _packet(header, _filled_strip(x, width)))


def clear(dev) -> None:
    """Blank the panel so each test starts from a known state."""
    for x in (0, 64):
        header = [REPORT_ID, CMD_SCREEN, x, 0, 64, HEIGHT]
        send(dev, _packet(header, [0x00] * (64 * (HEIGHT // 8))))
        time.sleep(0.05)


def ask(question: str) -> None:
    print(f"      -> {question}")
    input("      press Enter for the next test... ")


def main() -> int:
    dev = None
    for pid, name in PRODUCTS.items():
        dev = usb.core.find(idVendor=VENDOR, idProduct=pid)
        if dev is not None:
            print(f"Found {name} (1038:{pid:04x})\n")
            break
    if dev is None:
        return ("No Nova Pro DAC found. Is it plugged in, and is the ASM daemon "
                "stopped?  systemctl --user stop arctis-manager")

    print("Each test blanks the screen first. Note what you actually see —")
    print("especially test 2, which is the one that tells the two causes apart.\n")

    # 1 — Baseline. If this does not show, nothing below means anything.
    clear(dev)
    print("[1] LEFT strip alone (x=0, 64px wide)")
    strip(dev, 0, 64)
    ask("Is the LEFT half filled?  (expected: yes — this is the strip that already works)")

    # 2 — The decisive one: the right strip with no preceding packet.
    clear(dev)
    print("[2] RIGHT strip alone (x=64, 64px wide)  <-- THE DECISIVE TEST")
    strip(dev, 64, 64)
    ask("Where is the block: RIGHT half / LEFT half / nowhere?")

    # 3 — Both, back to back, no delay: exactly what ASM does today.
    clear(dev)
    print("[3] BOTH strips back to back, no delay (what ASM does today)")
    strip(dev, 0, 64)
    strip(dev, 64, 64)
    ask("Is the WHOLE screen filled, or only the left half?")

    # 4/5 — Same, with a pause. If these fill the screen and test 3 does not,
    # the firmware simply needs time between reports.
    for delay_ms in (20, 100):
        clear(dev)
        print(f"[{4 if delay_ms == 20 else 5}] BOTH strips with a {delay_ms} ms pause between them")
        strip(dev, 0, 64)
        time.sleep(delay_ms / 1000)
        strip(dev, 64, 64)
        ask("Is the WHOLE screen filled now?")

    # 6 — Narrower strips. If the panel only ever accepts one report per frame,
    # this fills just the leftmost quarter whatever the delay.
    clear(dev)
    print("[6] FOUR strips of 32px, 20 ms apart")
    for x in (0, 32, 64, 96):
        strip(dev, x, 32)
        time.sleep(0.02)
    ask("How much of the screen is filled: all / half / a quarter?")

    print("\nHanding the screen back to the DAC.")
    send(dev, _packet([REPORT_ID, CMD_RETURN_UI, 0, 0, 0, 0], [], CONTROL_REPORT_SIZE),
         control=True)
    print("Done — restart ASM with:  systemctl --user start arctis-manager")
    return 0


if __name__ == "__main__":
    sys.exit(main())
