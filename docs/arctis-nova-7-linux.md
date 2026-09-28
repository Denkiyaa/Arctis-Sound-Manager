---
title: Arctis Nova 7 on Linux
description: >-
  The Arctis Nova 7, 7X and 7P play sound on Linux out of the box, but their
  settings, battery level, ChatMix and equalizer need SteelSeries GG. Arctis
  Sound Manager brings them to Linux, natively, on PipeWire.
---

# Arctis Nova 7 on Linux

Plug the Nova 7's USB-C dongle into a Linux machine and the headset works as a
sound card: audio out, microphone in. What it does not give you is anything
SteelSeries GG handles on Windows — sidetone, the volume limiter, the
auto-off timer, the battery level, the equalizer, and a ChatMix dial that
actually balances something.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) (ASM)
speaks the headset's own USB protocol and brings those back, natively — no
Wine, no vendor driver.

## What you get

**Headset and microphone**

- Microphone volume and sidetone
- Brightness of the microphone's mute LED
- Volume limiter
- Auto-off timer
- Battery level and charging state
- Bluetooth: power and behaviour on an incoming call

**Audio, on PipeWire**

- **Game / Chat / Media / Output channels** as real sound devices, with a mixer
  and automatic routing of applications
- **ChatMix**: the dial on the ear cup balances Game against Chat — see
  [Arctis ChatMix on Linux](arctis-chatmix-linux.md)
- A **Sonar-style equalizer** per channel, with GG's game presets — see
  [SteelSeries Sonar on Linux](steelseries-sonar-linux.md)
- **Spatial audio** through HRIR convolution
- Microphone noise suppression, noise gate and compressor

The PlayStation edition **Nova 7P** has no ChatMix dial and no sidetone; ASM
shows the settings that model actually has and nothing else.

## Which models

The whole family is recognised, first and second generation:

- **Arctis Nova 7** and **Nova 7 Wireless Gen 2**
- **Arctis Nova 7X** (Xbox), including the v2, Gen 2 and White revisions
- **Arctis Nova 7P** (PlayStation), Gen 1 and Gen 2
- The **Diablo IV** edition

The two generations report the battery differently — in steps on the first,
in percent on the second — and ASM reads each the way its firmware sends it.
The [supported devices page](device_support.md) lists every USB ID; to check
yours:

```
lsusb | grep 1038
```

## Getting it

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

The script detects your distribution and installs the native package — Arch,
Fedora, Nobara, Debian, Ubuntu, Bazzite and SteamOS are covered. The
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation)
has the per-distribution commands.

Something not working? The [troubleshooting page](arctis-linux-troubleshooting.md)
covers the common cases.
