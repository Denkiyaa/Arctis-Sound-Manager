#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find the pixel layout of the Arctis Pro Wireless base's screen.

The base inherits the Siberia 840 screen protocol (its SteelSeries spec
includes siberia-840): 0xD0/0xD1 take the screen, 0xD2 draws a whole 128x48
frame. The commands are in the spec; the pixel layout is not. GG packs the
frame with `convert-to-column-packed-byte-format`, a builtin the spec only
names — and its siblings `-with-LSB` and `-with-LSB-y-flipped` are what other
DACs use, so the plain one is something else again. Guessing would only
produce a screen of noise for whoever guessed wrong (#305).

This draws the same picture in each candidate layout, one after the other:

    a 1-px border all around the screen
    a filled block in the TOP-LEFT corner
    the number of the layout, upright, in the middle

Exactly one should show all three the right way up. The others show stripes,
noise, or the picture mirrored — tell us the number that looked right.

Run it with the ASM daemon stopped, so nothing else is drawing to the panel:

    systemctl --user stop arctis-manager
    python3 oled_packing_probe.py
    systemctl --user start arctis-manager

It only draws to the screen — no setting is written — and the base's own
screen comes back at the end.
"""
from __future__ import annotations

import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing — install python-pyusb (or pip install --user pyusb)")

try:
    from PIL import Image, ImageDraw

    from arctis_sound_manager.oled_protocol import SiberiaOledProtocol
    from arctis_sound_manager.oled_renderer import _load_font
except ImportError:
    sys.exit("Run this on a machine with Arctis Sound Manager installed.")

VENDOR = 0x1038
PRODUCT = 0x1290  # Arctis Pro Wireless base station
INTERFACE = 0
WIDTH, HEIGHT = 128, 48
_FEATURE, _OUTPUT = 0x03, 0x02

PROTOCOL = SiberiaOledProtocol(report_id=0x00, width=WIDTH, height=HEIGHT,
                               frame_report_size=1025, control_report_size=33)


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


def take_screen(dev) -> None:
    for packet in PROTOCOL.build_preamble_packets():
        send(dev, packet, control=True)
        time.sleep(PROTOCOL.PREAMBLE_GAP_S)


def test_picture(number: int) -> bytes:
    image = Image.new("1", (WIDTH, HEIGHT), color=0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=1)
    draw.rectangle((0, 0, 23, 15), fill=1)
    font = _load_font(32)
    text = str(number)
    draw.text(((WIDTH - font.getlength(text)) // 2, 6), text, font=font, fill=1)
    return image.tobytes()


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

    answers = []
    try:
        for number, packing in enumerate(PROTOCOL.PACKINGS, start=1):
            PROTOCOL.packing = packing
            take_screen(dev)
            for packet in PROTOCOL.build_frame_packets(test_picture(number), WIDTH, HEIGHT):
                send(dev, packet)
            reply = input(f"[{number}] Border, block TOP-LEFT and an upright '{number}'? [y/N] ")
            if reply.strip().lower().startswith("y"):
                answers.append(f"{number} ({packing})")
    finally:
        send(dev, PROTOCOL.build_return_to_ui_packet(), control=True)
        usb.util.release_interface(dev, INTERFACE)
        if reattach:
            try:
                dev.attach_kernel_driver(INTERFACE)
            except usb.core.USBError:
                pass

    print()
    if answers:
        print("Looked right: " + ", ".join(answers))
    else:
        print("None looked right. Please describe what each number showed "
              "(stripes, mirrored, upside down, nothing at all).")
    print("Paste this output into the GitHub issue.")


if __name__ == "__main__":
    main()
