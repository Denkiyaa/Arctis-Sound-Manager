---
title: SteelSeries headset on Linux
description: >-
  A SteelSeries Arctis headset plays sound on Linux out of the box, but its
  settings, equalizer and ChatMix need SteelSeries GG, which is Windows-only.
  Arctis Sound Manager brings them to Linux, natively, on PipeWire.
---

# SteelSeries headset on Linux

Plug a SteelSeries Arctis headset into a Linux machine and it works as a sound
card straight away: audio out, microphone in. Everything else is missing —
sidetone, noise cancelling, the inactivity timer, battery level, the equalizer,
the ChatMix dial, the OLED screen on the base station. On Windows those come
from **SteelSeries GG**, and GG has no Linux build.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) is the
Linux application that fills that gap. It is free, open source (GPL-3.0), runs
natively — no Wine, no vendor driver — and is built on PipeWire.

## What works without it, and what does not

**Works out of the box:** playback and microphone, through the kernel's USB
audio driver. On recent kernels the `hid-steelseries` driver also reports the
battery level of some wireless models.

**Does not:** anything GG does. The headset's settings are sent over USB HID in
a protocol of its own, and the Game / Chat split, the equalizer and ChatMix are
software that runs on the PC, not in the headset. Linux has neither half until
something provides it.

## What Arctis Sound Manager adds

- **Headset settings** — sidetone, active noise cancelling and transparency,
  inactivity timeout, microphone volume, battery, and the base station's OLED
  screen, spoken over the headset's own USB protocol.
- **Game / Chat / Media / Output channels** as real PipeWire devices, with a
  mixer to balance them and automatic routing of applications.
- **A working ChatMix dial** — see [Arctis ChatMix on Linux](arctis-chatmix-linux.md).
- **A Sonar-style equalizer** per channel, with the Sonar game presets
  included — see [SteelSeries Sonar on Linux](steelseries-sonar-linux.md).
- **Spatial audio** through HRIR convolution, the counterpart of Sonar's
  virtual surround.
- **Microphone effects** — noise suppression, noise gate, compressor.

## Supported SteelSeries headsets

- Arctis Nova Pro Wireless, Nova Pro Wired, Nova Pro Omni, Nova Elite
- Arctis Nova 7 and Nova 7P (Gen 1 and Gen 2), Nova 5 Wireless, Nova 4,
  Nova 3 and Nova 3 Wireless
- Arctis 7, 7+, 7P, 7X, 9 Wireless, 1 Wireless, 5, Pro Wireless and
  Pro with GameDAC
- Arctis GameBuds

Model pages go into more detail:
[Nova Pro Wireless](arctis-nova-pro-wireless-linux.md),
[Nova 7](arctis-nova-7-linux.md), [Arctis 7](arctis-7-linux.md).

Each headset is described in a YAML profile, so adding one is data rather than
code; the [device configuration specs](device_configuration_file_specs.md)
explain the format. A headset ASM cannot talk to — or another brand entirely —
can still use the channels, the mixer and the equalizer in generic mode.

## Getting it

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

The script detects your distribution and installs the native package: Arch and
its derivatives (AUR), Fedora and Nobara (COPR), Debian and Ubuntu (PPA / .deb),
and Bazzite, SteamOS and Silverblue through Distrobox. The
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation) has
the per-distribution commands if you would rather run them yourself.

Something not working? See [troubleshooting](arctis-linux-troubleshooting.md).

Missing a model, or a setting that does nothing on yours? Open an
[issue](https://github.com/loteran/Arctis-Sound-Manager/issues) — the
[hardware questions](HARDWARE-QUESTIONS.md) page says what to capture.
