#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the Arctis Pro Wireless base's screen layout, now that it is known.

oled_packing_probe.py and oled_geometry_probe.py guessed (#305). The layout is
no longer a guess: SteelSeries GG packs the siberia-840 frame with
utils.defConvertToColumnPackedByteFormat, whose disassembly (GG 118) reads:

    byte (y // 8) * 128 + x, bit y % 8   (bit 0 = top pixel)

i.e. 8-row pages left to right, then top to bottom — and a capture of GG on
the AIDA64 forum (topic 8288) shows the same frame. What is left to check is
how the frame must be sent: the geometry probe drew different things on each
run, so the transport is suspect too. This draws one test picture (border,
filled block top-left, a big digit) four ways:

  1. as GG does: take the screen (0xD0, 0xD1), then the frame
  2. the frame alone, no 0xD0/0xD1 (as in the AIDA64 capture)
  3. as 1, then the frame resent for 2 s at GG's 35 ms pace
  4. as 1, bit order flipped (bit 7 = top) — should look wrong

Note which numbers show an upright digit inside a full border, with the
block top-left.

Run it with the ASM daemon stopped, so nothing else draws on the panel:
    systemctl --user stop arctis-manager
    python3 oled_page_probe.py
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
WIDTH, HEIGHT = 128, 48
# Report id 0x00 = unnumbered reports: the id byte is not sent, so the
# siberia-840 spec's 1025-byte oled_display and 33-byte controls are 1024/32.
FRAME_REPORT, CONTROL_REPORT = 1024, 32
PREAMBLE_GAP_S = 0.25
COMMAND_GAP_S = 0.035  # siberia-840 time-between-commands
RESEND_S = 2.0

# 5x7 digits, one string per row, '#' = lit.
DIGITS = {
    1: ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    2: [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    3: ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    4: ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
}
DIGIT_SCALE = 5


def test_picture(number: int) -> list[list[bool]]:
    """Border all around, a filled block top-left, the number in the middle."""
    image = [[False] * WIDTH for _ in range(HEIGHT)]
    for x in range(WIDTH):
        image[0][x] = image[HEIGHT - 1][x] = True
    for y in range(HEIGHT):
        image[y][0] = image[y][WIDTH - 1] = True
    for y in range(3, 13):
        for x in range(3, 23):
            image[y][x] = True
    rows = DIGITS[number]
    left = (WIDTH - 5 * DIGIT_SCALE) // 2
    top = (HEIGHT - 7 * DIGIT_SCALE) // 2
    for row, line in enumerate(rows):
        for col, cell in enumerate(line):
            if cell == "#":
                for dy in range(DIGIT_SCALE):
                    for dx in range(DIGIT_SCALE):
                        image[top + row * DIGIT_SCALE + dy][left + col * DIGIT_SCALE + dx] = True
    return image


def pack_pages(image: list[list[bool]], top_bit_first: bool = False) -> bytes:
    out = bytearray(WIDTH * HEIGHT // 8)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if image[y][x]:
                bit = 7 - y % 8 if top_bit_first else y % 8
                out[(y // 8) * WIDTH + x] |= 1 << bit
    return bytes(out)


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


def take_screen(dev) -> None:
    # set_custom_write_mode then set_custom_write_offset (siberia-840 spec).
    send(dev, report([0xD0, 0x01, 0x0F], b"", CONTROL_REPORT), control=True)
    time.sleep(PREAMBLE_GAP_S)
    send(dev, report([0xD1, 0x00, 0x10], b"", CONTROL_REPORT), control=True)
    time.sleep(PREAMBLE_GAP_S)


def give_back_screen(dev) -> None:
    send(dev, report([0xD0, 0x00, 0x00], b"", CONTROL_REPORT), control=True)
    time.sleep(1.0)


def draw(dev, frame: bytes) -> None:
    send(dev, report([0xD2, 0x00], frame, FRAME_REPORT))


def variant_gg(dev, number: int) -> None:
    take_screen(dev)
    draw(dev, pack_pages(test_picture(number)))


def variant_no_preamble(dev, number: int) -> None:
    draw(dev, pack_pages(test_picture(number)))


def variant_resend(dev, number: int) -> None:
    take_screen(dev)
    frame = pack_pages(test_picture(number))
    deadline = time.monotonic() + RESEND_S
    while time.monotonic() < deadline:
        draw(dev, frame)
        time.sleep(COMMAND_GAP_S)


def variant_flipped(dev, number: int) -> None:
    take_screen(dev)
    draw(dev, pack_pages(test_picture(number), top_bit_first=True))


VARIANTS = [
    ("as GG: take the screen, then the frame", variant_gg),
    ("frame alone, no 0xD0/0xD1", variant_no_preamble),
    ("as GG, frame resent for 2 s", variant_resend),
    ("as GG, bit order flipped", variant_flipped),
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

    answers = []
    try:
        for number, (label, run) in enumerate(VARIANTS, start=1):
            # Start each variant from the base's own screen, so one cannot
            # inherit what the previous one left behind.
            give_back_screen(dev)
            run(dev, number)
            answer = input(f"[{number}/{len(VARIANTS)}] {label}\n"
                           f"   Upright '{number}', full border, block top-left? [y/n] ")
            if answer.strip().lower().startswith("y"):
                answers.append(str(number))
    finally:
        send(dev, report([0xD0, 0x00, 0x00], b"", CONTROL_REPORT), control=True)
        usb.util.release_interface(dev, INTERFACE)
        if reattach:
            try:
                dev.attach_kernel_driver(INTERFACE)
            except usb.core.USBError:
                pass

    print(f"\nLooked right: {', '.join(answers) or 'none'}")
    print("Please post this line in the GitHub issue (a photo of any wrong one helps too).")


if __name__ == "__main__":
    main()
