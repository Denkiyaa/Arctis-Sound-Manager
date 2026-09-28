---
title: SteelSeries Arctis on Linux — troubleshooting
description: >-
  Fixes for the common problems with a SteelSeries Arctis headset on Linux:
  no sound, an application playing on the wrong device, the ChatMix dial doing
  nothing, the headset not recognised, and the GUI failing to start.
---

# SteelSeries Arctis on Linux — troubleshooting

The problems below are the ones that come up most in
[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager)'s
issue tracker. Most are about where audio is routed rather than about the
headset itself.

## No sound, or an application plays on the wrong device

PipeWire remembers, per application, the last output it used. An application
that once played on HDMI, a TV or another sound card will keep going there, and
you hear nothing in the headset.

- Click **Bring audio to headset**, on the Home page or in the tray menu. It
  moves every application playing on a non-Arctis device back to the headset,
  and leaves those already on a Game, Chat or Media channel alone.
- An application playing somewhere ASM does not manage — typically when the
  system default output is not one of ASM's channels — is listed on the Home
  page under **Other applications**, with the device it plays on, so you can
  send it to a channel from there.

## The ChatMix dial does nothing

The dial balances ASM's **Game** channel against its **Chat** channel. If the
game and the voice client both play on the same channel, or on neither, there
is nothing for it to balance. Put the voice client (Discord, TeamSpeak…) on
Chat and the game on Game, from ASM's mixer or your desktop's sound settings.
[Arctis ChatMix on Linux](arctis-chatmix-linux.md) explains how the dial works.

## The equalizer and virtual surround turned themselves off

A banner at the top of the **Sonar** page means ASM is in *safe mode*: PipeWire's
filter-chain, which runs the equalizer, crashed repeatedly, and ASM switched to
a flat, stable audio path rather than leave sound cutting in and out. Click
**Re-enable EQ** to try again; if the crash is still there, safe mode comes back
on its own. It is also cleared automatically after an ASM or PipeWire update,
the usual way the underlying crash gets fixed.

## The headset is not recognised, or its settings do nothing

ASM talks to the headset over USB, which needs the udev rules its package
installs. If the headset shows as offline, or right after an update:

1. Unplug the dongle or base station and plug it back in.
2. If that is not enough, apply the rules to the device already connected:
   ```
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```
3. Still nothing? Reinstall ASM from your distribution's package (AUR, COPR,
   PPA or .deb), which writes the rules again.

If your model is simply not in the [supported devices list](device_support.md),
ASM runs in generic mode — channels, mixer and equalizer work on any output —
and an [issue](https://github.com/loteran/Arctis-Sound-Manager/issues) with the
output of `lsusb | grep 1038` is how a new profile gets added.

## The GUI does not start: `undefined symbol … Qt_6_PRIVATE_API`

PySide6 and the Qt libraries are at different versions. PySide6 must be built
against the exact Qt installed, so this is almost always a partial upgrade:

- On Arch and derivatives, run a full `sudo pacman -Syu` rather than upgrading
  one package.
- Remove any PySide6 installed with `pip` in `~/.local`, which shadows the
  distribution's matching one.

## Reporting a bug

Open ASM → **Help** → **Report a bug**. It gathers the diagnostic that almost
every issue needs — versions, PipeWire state, USB devices, udev rules, recent
logs — and opens a pre-filled GitHub issue. From a terminal:

```
asm-cli diagnose -o /tmp/asm.txt
```

The [hardware questions](HARDWARE-QUESTIONS.md) page says what to capture when
a setting has no effect on your model.
