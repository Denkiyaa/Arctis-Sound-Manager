#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure how the Arctis Pro Wireless base's screen lays out its bytes.

oled_packing_probe.py drew a picture in four guessed layouts and none came
out right (#305): borders turned into rows of dots ~6 px apart, whatever the
bit order. So it is the byte addressing that is unknown, not just the bits.
Rather than guess again, this draws raw byte patterns whose photo measures
the layout directly:

  1. all 768 bytes of a frame lit    -> which part of the screen a frame covers
  2. only bytes 0-15 lit (0xFF)      -> where byte 0 lands, and the shape of 16 bytes
  3. bytes 0-127 set to 0x0F         -> which bit of a byte is which pixel
  4. only bytes 128-143 lit (0xFF)   -> how far apart byte 0 and byte 128 are

Take a photo of each, straight on, then press Enter for the next one.

Run it with the ASM daemon stopped, so nothing else draws on the panel:
    systemctl --user stop arctis-manager
    python3 oled_geometry_probe.py
    systemctl --user start arctis-manager

It only draws on the screen — no setting is written — and the base's own
screen comes back at the end. Needs only pyusb, not ASM itself.
"""

from __future__ import annotations

import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing — install python-pyusb (or pip install --user pyusb)")

VENDOR = 0x1038
PRODUCT = 0x1290  # Arctis Pro Wireless base station
INTERFACE = 0
_FEATURE, _OUTPUT = 0x03, 0x02
FRAME_BYTES = 768  # 128 x 48 at 1 bit per pixel
# Report id 0x00 = unnumbered reports: the id byte is not sent, so the
# siberia-840 spec's 1025-byte oled_display and 33-byte controls are 1024/32.
FRAME_REPORT, CONTROL_REPORT = 1024, 32
PREAMBLE_GAP_S = 0.25


def report(header: list[int], body: bytes, size: int) -> list[int]:
    payload = header + list(body)
    return (payload + [0] * size)[:size]


def send(dev, packet: list[int], control: bool = False) -> None:
    bm = usb.util.build_request_type(
        direction=usb.util.CTRL_OUT,
        type=usb.util.CTRL_TYPE_CLASS,
        recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
    )
    wvalue = (_OUTPUT if control else _FEATURE) << 8
    try:
        dev.ctrl_transfer(bm, 0x09, wvalue, INTERFACE, packet, timeout=1000)
    except usb.core.USBError as e:
        # An unacknowledged report can still have been drawn: report, go on.
        print(f"   !! USB error: {e}")


def draw(dev, frame: bytes) -> None:
    # set_custom_write_mode then set_custom_write_offset take the screen,
    # oled_display draws (siberia-840 spec).
    send(dev, report([0xD0, 0x01, 0x0F], b"", CONTROL_REPORT), control=True)
    time.sleep(PREAMBLE_GAP_S)
    send(dev, report([0xD1, 0x00, 0x10], b"", CONTROL_REPORT), control=True)
    time.sleep(PREAMBLE_GAP_S)
    send(dev, report([0xD2, 0x00], frame, FRAME_REPORT))


def pattern(start: int, count: int, value: int) -> bytes:
    frame = bytearray(FRAME_BYTES)
    frame[start:start + count] = bytes([value]) * count
    return bytes(frame)


PATTERNS = [
    ("all 768 bytes lit", pattern(0, FRAME_BYTES, 0xFF)),
    ("bytes 0-15 lit", pattern(0, 16, 0xFF)),
    ("bytes 0-127 = 0x0F", pattern(0, 128, 0x0F)),
    ("bytes 128-143 lit", pattern(128, 16, 0xFF)),
]


def main() -> None:
    dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
    if dev is None:
        sys.exit("No Arctis Pro Wireless base (1038:1290) found. Is it plugged in?")

    reattach = False
    try:
        if dev.is_kernel_driver_active(INTERFACE):
            dev.detach_kernel_driver(INTERFACE)
            reattach = True
        usb.util.claim_interface(dev, INTERFACE)
    except usb.core.USBError as e:
        sys.exit(f"Cannot take interface {INTERFACE}: {e}\n"
                 "Is the ASM daemon still running? Stop it first (see the top of this file).")

    try:
        for number, (label, frame) in enumerate(PATTERNS, start=1):
            draw(dev, frame)
            input(f"[{number}/{len(PATTERNS)}] {label} — take a photo, then press Enter ")
    finally:
        send(dev, report([0xD0, 0x00, 0x00], b"", CONTROL_REPORT), control=True)
        usb.util.release_interface(dev, INTERFACE)
        if reattach:
            try:
                dev.attach_kernel_driver(INTERFACE)
            except usb.core.USBError:
                pass

    print("\nDone. Please post the 4 photos in the GitHub issue, in order.")


if __name__ == "__main__":
    main()
