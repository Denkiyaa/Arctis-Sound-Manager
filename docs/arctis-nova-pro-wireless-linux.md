---
title: Arctis Nova Pro Wireless on Linux
description: >-
  The Arctis Nova Pro Wireless plays sound on Linux out of the box, but its
  base station settings, OLED screen, ChatMix and equalizer need SteelSeries GG.
  Arctis Sound Manager brings them to Linux, natively, on PipeWire.
---

# Arctis Nova Pro Wireless on Linux

The Nova Pro Wireless is the headset that loses the most without SteelSeries
GG. Plugged into Linux, the base station is a sound card and nothing more: the
headset plays and the microphone records, but the screen on the base station
shows its own menus only, the ChatMix dial balances nothing, and none of the
settings GG exposes can be reached from the desktop.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) (ASM)
talks to the base station over its own USB protocol and gives all of that back,
natively — no Wine, no vendor driver.

## What you get

**Headset and microphone**

- Headset gain (low / high)
- Microphone volume, sidetone and mute-LED brightness
- Noise cancelling / transparency mode, shown live as you switch it with the
  headset's button
- Auto-off timer
- Battery level of the headset **and** of the spare battery charging in the
  base station

**Base station**

- The **OLED screen**: keep the original display or build your own from the
  time, battery, profile, EQ preset and weather, with brightness, timeout and
  font size
- The **line out** (the AUX output on the back), with its own volume — useful
  when speakers hang off the base station
- Wireless mode, and Bluetooth: power and behaviour on an incoming call

**Audio, on PipeWire**

- **Game / Chat / Media / Output channels** as real sound devices, with a mixer
  and automatic routing of applications
- **ChatMix**: the dial on the base station balances Game against Chat — see
  [Arctis ChatMix on Linux](arctis-chatmix-linux.md)
- A **Sonar-style equalizer** per channel, with GG's game presets — see
  [SteelSeries Sonar on Linux](steelseries-sonar-linux.md)
- **Spatial audio** through HRIR convolution
- Microphone noise suppression, noise gate and compressor

## Which models

Every Nova Pro Wireless variant is recognised: the original (USB ID
`1038:12e0`), the Xbox edition **Nova Pro Wireless X** (`1038:12e5`) and its
later revision (`1038:225d`). The wired **Nova Pro** and the **Nova Pro Omni**
have their own profiles; the [supported devices page](device_support.md) lists
every model.

To check which one you have:

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
