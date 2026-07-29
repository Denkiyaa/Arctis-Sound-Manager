# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_eq_mode_exclusivity.py — the two equalisers must never stack.

ASM has two equalisers and only one of them may shape the sound at a time:

* **Sonar mode** equalises in software, in the filter chain. The on-device
  curve has to be flat, or the headset colours the sound underneath the Sonar
  curve and neither setting means what it says.
* **Custom EQ mode** equalises in the headset. The Sonar chain is already out
  of the path in that mode (the daemon points the loopbacks straight at the
  physical output), so the on-device curve is the whole setting.

`.eq_mode` is written from four places — both mode toggles, a restored profile,
and the Sonar-forcing path in equalizer_page — so the daemon reconciles the
hardware side from its status poll rather than trusting each writer to
remember a D-Bus call.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

# Profile → the frame that flattens its on-device EQ.
# The Nova Pro families switch to their Flat preset, which leaves the stored
# custom curve untouched in its slot. Families without a preset command get a
# curve that is flat instead.
FLAT_BY_PRESET = {
    "nova_pro_wireless.yaml": [0x06, 0x2E, 0x00],
    "arctis_nova_pro_wired.yaml": [0x06, 0x2E, 0x00],
}
FLAT_BY_CURVE = ["nova_7_discrete_battery.yaml", "nova_4.yaml"]


def _config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(YAML(typ="safe").load(DEVICES / name))


def _engine(profile: str, mode: str, tmp_path: Path,
            stored: list[int] | None = None) -> MagicMock:
    """A CoreEngine stand-in with HOME pointing at *tmp_path*."""
    cfg = _config(profile)
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.logger = MagicMock()
    engine.get_command_endpoint_address.return_value = 0
    engine._applied_eq_mode = None
    engine.send_command = MagicMock()

    (tmp_path / ".config" / "arctis_manager").mkdir(parents=True)
    (tmp_path / ".config" / "arctis_manager" / ".eq_mode").write_text(mode)
    if stored is not None:
        (tmp_path / ".config" / "arctis_manager" / "eq_bands.json").write_text(
            json.dumps(stored))

    # Real implementations for everything under test; the mode reader is a
    # staticmethod that reads $HOME, monkeypatched by the caller's fixture.
    engine.has_hardware_eq = lambda: CoreEngine.has_hardware_eq(engine)
    engine._read_eq_mode_is_sonar = CoreEngine._read_eq_mode_is_sonar
    engine.send_eq_command = lambda bands: CoreEngine.send_eq_command(engine, bands)
    engine._select_custom_eq_preset = (
        lambda ep: CoreEngine._select_custom_eq_preset(engine, ep))
    engine._apply_stored_eq = lambda: CoreEngine._apply_stored_eq(engine)
    engine._flatten_hardware_eq = lambda: CoreEngine._flatten_hardware_eq(engine)
    engine.reconcile_hardware_eq_mode = (
        lambda: CoreEngine.reconcile_hardware_eq_mode(engine))
    return engine


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ── Sonar mode: the headset must be flat ────────────────────────────────────

@pytest.mark.parametrize("profile,expected", sorted(FLAT_BY_PRESET.items()))
def test_sonar_mode_selects_the_flat_preset(profile, expected, tmp_path):
    engine = _engine(profile, "sonar", tmp_path, stored=[40] * 10)

    assert engine.reconcile_hardware_eq_mode() is True

    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert frames == [expected], "Flat preset, and nothing else"


@pytest.mark.parametrize("profile", FLAT_BY_CURVE)
def test_sonar_mode_writes_a_flat_curve_when_there_is_no_preset_command(
        profile, tmp_path):
    """No opcode is invented: these families get gains that are all 0 dB."""
    engine = _engine(profile, "sonar", tmp_path, stored=[40] * 10)

    assert engine.reconcile_hardware_eq_mode() is True

    gains = engine.send_command.call_args_list[0].args[0][1:]
    zero = _config(profile).hardware_eq_zero
    assert gains == [zero] * 10


def test_sonar_mode_never_reinstates_the_stored_curve_at_startup(tmp_path):
    """The saved curve must not come back underneath the software EQ."""
    engine = _engine("nova_pro_wireless.yaml", "sonar", tmp_path,
                     stored=[0, 40] * 5)

    engine.reconcile_hardware_eq_mode()

    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert all(frame[1] != 0x33 for frame in frames), "no curve may be written"


def test_a_write_in_sonar_mode_does_not_switch_the_headset_to_custom(tmp_path):
    """Exclusivity holds whoever calls, not just when callers are polite."""
    engine = _engine("nova_pro_wireless.yaml", "sonar", tmp_path)

    engine.send_eq_command([30] * 10)

    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert [0x06, 0x2E, 0x04] not in frames


# ── Custom mode: the stored curve applies, and is activated ─────────────────

def test_custom_mode_writes_the_stored_curve_and_selects_it(tmp_path):
    engine = _engine("nova_pro_wireless.yaml", "custom", tmp_path,
                     stored=[0, 5, 10, 15, 20, 25, 30, 35, 40, 20])

    assert engine.reconcile_hardware_eq_mode() is True

    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert frames[0] == [0x06, 0x33, 0, 5, 10, 15, 20, 25, 30, 35, 40, 20]
    assert frames[1] == [0x06, 0x2E, 0x04], "the Custom slot must be selected"


def test_custom_mode_with_no_saved_curve_writes_nothing(tmp_path):
    """Nothing to restore, and no reason to disturb what the headset holds."""
    engine = _engine("nova_pro_wireless.yaml", "custom", tmp_path)

    assert engine.reconcile_hardware_eq_mode() is False
    engine.send_command.assert_not_called()


# ── The reconciler itself ───────────────────────────────────────────────────

def test_an_unchanged_mode_sends_nothing_on_later_passes(tmp_path):
    """This runs on every status poll — it must be silent when nothing moved."""
    engine = _engine("nova_pro_wireless.yaml", "sonar", tmp_path)

    assert engine.reconcile_hardware_eq_mode() is True
    engine.send_command.reset_mock()

    for _ in range(5):
        assert engine.reconcile_hardware_eq_mode() is False
    engine.send_command.assert_not_called()


def test_switching_mode_is_picked_up_without_anyone_calling_dbus(tmp_path):
    """The four writers of .eq_mode need no cooperation beyond the file."""
    engine = _engine("nova_pro_wireless.yaml", "sonar", tmp_path,
                     stored=[40] * 10)
    engine.reconcile_hardware_eq_mode()
    engine.send_command.reset_mock()

    (tmp_path / ".config" / "arctis_manager" / ".eq_mode").write_text("custom")

    assert engine.reconcile_hardware_eq_mode() is True
    frames = [call.args[0] for call in engine.send_command.call_args_list]
    assert frames[-1] == [0x06, 0x2E, 0x04]


def test_a_headset_without_an_on_device_eq_is_left_alone(tmp_path):
    engine = _engine("arctis_9.yaml", "custom", tmp_path, stored=[40] * 10)

    assert engine.reconcile_hardware_eq_mode() is False
    engine.send_command.assert_not_called()
