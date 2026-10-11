# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Streamer mode backend: state file, gains, conf generator, links, setting."""

import json
import logging
import re
import types
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager import pw_utils, sonar_to_pipewire as stp, stream_mix
from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService
from arctis_sound_manager.settings import GeneralSettings
from arctis_sound_manager.stream_mix import (
    STREAM_CHANNELS, STREAM_MIX_INPUT_NODE, STREAM_MIX_OUTPUT_NODE,
    apply_stream_mix, load_stream_mix, save_stream_mix_channel, stream_gain,
)


@pytest.fixture(autouse=True)
def _state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_mix, "STREAM_MIX_FILE", tmp_path / "stream_mix.json")
    return tmp_path / "stream_mix.json"


# ── state file ───────────────────────────────────────────────────────────────

def test_defaults_when_no_file():
    state = load_stream_mix()
    assert list(state) == list(STREAM_CHANNELS)
    assert all(v == {"volume": 100, "muted": False} for v in state.values())


def test_save_and_reload_roundtrip():
    save_stream_mix_channel("chat", volume=40)
    save_stream_mix_channel("chat", muted=True)
    save_stream_mix_channel("mic", volume=75, muted=False)
    state = load_stream_mix()
    assert state["chat"] == {"volume": 40, "muted": True}      # second save kept the volume
    assert state["mic"] == {"volume": 75, "muted": False}
    assert state["game"] == {"volume": 100, "muted": False}


def test_save_unknown_channel_is_ignored(_state_file):
    save_stream_mix_channel("bogus", volume=10)
    assert not _state_file.exists()


@pytest.mark.parametrize("raw", ["", "{not json", "[1, 2]", "null", '{"game": 5}'])
def test_corrupt_file_reads_as_defaults(_state_file, raw):
    _state_file.write_text(raw)
    assert load_stream_mix()["game"] == {"volume": 100, "muted": False}


def test_out_of_range_values_are_clamped(_state_file):
    _state_file.write_text(json.dumps({
        "game": {"volume": 500, "muted": True},
        "chat": {"volume": -20},
        "media": {"volume": "loud", "muted": "yes"},
        "aux": {"volume": True},
    }))
    state = load_stream_mix()
    assert state["game"] == {"volume": 100, "muted": True}
    assert state["chat"]["volume"] == 0
    assert state["media"] == {"volume": 100, "muted": False}
    assert state["aux"]["volume"] == 100


def test_save_clamps(_state_file):
    save_stream_mix_channel("game", volume=250)
    assert json.loads(_state_file.read_text())["game"]["volume"] == 100


# ── gains ────────────────────────────────────────────────────────────────────

def test_stream_gain():
    assert stream_gain(100, False) == 1.0
    assert stream_gain(50, False) == pytest.approx(0.125)
    assert stream_gain(0, False) == 0.0
    assert stream_gain(100, True) == 0.0


def test_apply_stream_mix_is_one_call_with_ten_keys():
    state = {ch: {"volume": 100, "muted": False} for ch in STREAM_CHANNELS}
    state["chat"] = {"volume": 50, "muted": False}
    state["mic"] = {"volume": 100, "muted": True}
    with patch("arctis_sound_manager.pw_utils.set_filter_controls", return_value=True) as sfc:
        assert apply_stream_mix(state) is True
    sfc.assert_called_once()
    node, controls = sfc.call_args.args
    assert node == STREAM_MIX_INPUT_NODE
    assert set(controls) == {f"mix{s}:Gain {i}" for s in "LR" for i in range(1, 6)}
    assert len(controls) == 10
    assert controls["mixL:Gain 2"] == controls["mixR:Gain 2"] == pytest.approx(0.125)
    assert controls["mixL:Gain 5"] == controls["mixR:Gain 5"] == 0.0
    assert controls["mixL:Gain 1"] == 1.0


def test_apply_stream_mix_uses_saved_state_and_never_raises():
    save_stream_mix_channel("aux", muted=True)
    with patch("arctis_sound_manager.pw_utils.set_filter_controls", return_value=False) as sfc:
        assert apply_stream_mix() is False
    assert sfc.call_args.args[1]["mixL:Gain 4"] == 0.0
    with patch("arctis_sound_manager.pw_utils.set_filter_controls", side_effect=RuntimeError):
        assert apply_stream_mix() is False


# ── conf generator ───────────────────────────────────────────────────────────

@pytest.fixture
def conf(tmp_path):
    return stp.generate_stream_mix_conf(output_path=tmp_path / "x.conf")


def _node_names(text):
    return re.findall(r"name = (\w+)", text.split("links = [")[0])


def test_conf_braces_balanced(conf):
    assert conf.count("{") == conf.count("}")
    assert conf.count("[") == conf.count("]")


def test_conf_has_27_inputs_and_positions(conf):
    inputs = re.search(r"inputs\s*=\s*\[(.*?)\]", conf).group(1)
    assert len(re.findall(r'"[^"]+:In"', inputs)) == 27
    assert "audio.channels     = 27" in conf
    assert "AUX0 AUX1" in conf and "AUX26 ]" in conf
    assert "AUX27" not in conf


def test_conf_links_reference_existing_nodes(conf):
    names = set(_node_names(conf))
    refs = re.findall(r'(?:output|input) = "(\w+):', conf)
    assert refs and set(refs) <= names
    for ref in re.findall(r'"(\w+):In"', re.search(r"inputs\s*=\s*\[(.*?)\]", conf).group(1)):
        assert ref in names
    assert {"mixL", "mixR"} <= names


def test_conf_itu_downmix_gains(conf):
    # Game FC folds into both sides at -3 dB, LFE is never linked anywhere.
    assert re.search(r"name = gmixL\s+control = \{[^}]*\"Gain 2\" = 0\.7071", conf)
    assert re.search(r"name = gmixR\s+control = \{[^}]*\"Gain 2\" = 0\.7071", conf)
    assert 'output = "gFC:Out"  input = "gmixL:In 2"' in conf
    assert 'output = "gFC:Out"  input = "gmixR:In 2"' in conf
    for prefix in "gma":
        assert f'"{prefix}LFE:Out"' not in conf


def test_conf_final_mix_slot_order_and_mic_fanout(conf):
    for side in "LR":
        assert f'output = "gmix{side}:Out"  input = "mix{side}:In 1"' in conf
        assert f'output = "chat{side}:Out"  input = "mix{side}:In 2"' in conf
        assert f'output = "mmix{side}:Out"  input = "mix{side}:In 3"' in conf
        assert f'output = "amix{side}:Out"  input = "mix{side}:In 4"' in conf
        assert f'output = "mic:Out"  input = "mix{side}:In 5"' in conf
    assert conf.count("name = mic ") == 1   # one copy node, fanned to both sides


def test_conf_node_props(conf):
    cap, play = conf.split("capture.props")[1].split("playback.props")
    assert f'node.name          = "{STREAM_MIX_INPUT_NODE}"' in cap
    assert "media.class        = Audio/Sink/Internal" in cap
    assert f'node.name          = "{STREAM_MIX_OUTPUT_NODE}"' in play
    assert "media.class        = Audio/Source" in play
    for part in (cap, play):
        assert "node.autoconnect   = false" in part
    assert "node.pause-on-idle = false" in cap and "node.linger        = true" in cap
    assert "audio.position     = [ FL FR ]" in play


def test_conf_description_falls_back(conf):
    assert 'node.description = "Arctis Stream Mix"' in conf


def test_conf_description_uses_device_name(tmp_path):
    with patch.object(stp, "_device_attached", return_value=True), \
         patch.object(stp.device_state, "get_channel_label", return_value="Arctis Nova 7"):
        text = stp.generate_stream_mix_conf(output_path=tmp_path / "x.conf")
    assert 'node.description = "Arctis Nova 7 Stream Mix"' in text


def test_conf_initial_gains_come_from_saved_state(tmp_path):
    save_stream_mix_channel("chat", volume=50)
    save_stream_mix_channel("mic", muted=True)
    text = stp.generate_stream_mix_conf(output_path=tmp_path / "x.conf")
    assert re.search(r'name = mixL\s+control = \{ "Gain 1" = 1\.000000  "Gain 2" = 0\.125000'
                     r'.*"Gain 5" = 0\.000000', text)


def test_conf_written_atomically(tmp_path):
    out = tmp_path / "d" / "sonar-stream-mix.conf"
    stp.generate_stream_mix_conf(output_path=out)
    assert out.read_text().startswith("# Auto-generated by Arctis Sound Manager")


def test_stream_mix_conf_is_an_asm_conf():
    assert "sonar-stream-mix.conf" in stp._ASM_CONF_NAMES


# ── conf presence follows streamer_mode, not the EQ mode ─────────────────────

@pytest.fixture
def conf_dir(tmp_path, monkeypatch):
    d = tmp_path / "filter-chain.conf.d"
    d.mkdir()
    monkeypatch.setattr(stp, "_CONF_DIR", d)
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "no-marker")
    return d


def test_sync_creates_removes_and_is_quiet_when_unchanged(conf_dir):
    path = conf_dir / "sonar-stream-mix.conf"
    assert stp.sync_stream_mix_conf(False) is False
    assert stp.sync_stream_mix_conf(True) is True and path.exists()
    assert stp.sync_stream_mix_conf(True) is False          # no restart owed
    path.write_text("custom")
    assert stp.sync_stream_mix_conf(True) is False and path.read_text() == "custom"
    assert stp.sync_stream_mix_conf(False) is True and not path.exists()


def test_sync_does_nothing_in_safe_mode(conf_dir, monkeypatch):
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)
    assert stp.sync_stream_mix_conf(True) is False
    assert not (conf_dir / "sonar-stream-mix.conf").exists()


# ── links ────────────────────────────────────────────────────────────────────

def _node(i, name):
    return {"id": i, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name}}}


def _port(pid, nid, direction, ch):
    return {"id": pid, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": nid, "port.direction": direction,
                               "audio.channel": ch}}}


def _link(lid, onode, oport, inode, iport):
    return {"id": lid, "type": "PipeWire:Interface:Link",
            "info": {"props": {"link.output.node": onode, "link.output.port": oport,
                               "link.input.node": inode, "link.input.port": iport}}}


_SURR = ("FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR")
MIX = 900


def _graph(game=_SURR, chat=("FL", "FR"), media=_SURR, aux=None, mic=True, extra=()):
    d = [_node(MIX, STREAM_MIX_INPUT_NODE)]
    d += [_port(1000 + n, MIX, "in", f"AUX{n}") for n in range(27)]
    for nid, base, name, chans in ((10, 100, "Arctis_Game", game), (11, 200, "Arctis_Chat", chat),
                                   (12, 300, "Arctis_Media", media), (13, 400, "Arctis_Aux", aux)):
        if chans is None:
            continue
        d.append(_node(nid, name))
        d += [_port(base + i, nid, "out", c) for i, c in enumerate(chans)]
    if mic:
        d += [_node(14, "effect_output.sonar-micro-eq"), _port(500, 14, "out", "MONO")]
    return d + list(extra)


@pytest.fixture
def pwlink(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(pw_utils.subprocess, "run", fake_run)
    return calls


def _is_link(c):
    return c[0].endswith("pw-link")


def _created(calls):
    return {(int(c[1]), int(c[2])) for c in calls if _is_link(c) and c[1] != "-d"}


def _deleted(calls):
    return {(int(c[2]), int(c[3])) for c in calls if _is_link(c) and c[1] == "-d"}


def test_links_full_8ch_graph(pwlink):
    assert stp.ensure_stream_mix_links(_graph(aux=_SURR)) is True
    made = _created(pwlink)
    assert len(made) == 8 + 2 + 8 + 8 + 1
    assert (102, 1002) in made            # Game FC -> AUX2
    assert (103, 1003) in made            # LFE is still delivered; the conf drops it
    assert (200, 1008) in made and (201, 1009) in made   # Chat
    assert (300, 1010) in made and (307, 1017) in made   # Media
    assert (400, 1018) in made and (407, 1025) in made   # Aux
    assert (500, 1026) in made            # mic -> AUX26
    assert not _deleted(pwlink)


def test_links_2ch_sinks_only_fl_fr(pwlink):
    stp.ensure_stream_mix_links(_graph(game=("FL", "FR"), media=("FL", "FR")))
    assert _created(pwlink) == {(100, 1000), (101, 1001), (200, 1008), (201, 1009),
                                (300, 1010), (301, 1011), (500, 1026)}


def test_links_aux_absent_is_skipped_not_failed(pwlink):
    assert stp.ensure_stream_mix_links(_graph(aux=None)) is True
    assert not any(out >= 400 and out < 500 for out, _ in _created(pwlink))


def test_links_already_linked_makes_no_call(pwlink):
    first = _graph(aux=_SURR)
    stp.ensure_stream_mix_links(first)
    links = [_link(2000 + i, 0, o, MIX, i_) for i, (o, i_) in enumerate(sorted(_created(pwlink)))]
    pwlink.clear()
    assert stp.ensure_stream_mix_links(_graph(aux=_SURR, extra=links)) is True
    assert pwlink == []


def test_links_stray_into_input_removed_source_side_preserved(pwlink):
    ok_link = _link(2000, 10, 100, MIX, 1000)
    stray_into_input = _link(2001, 77, 7700, MIX, 1005)           # someone else feeding AUX5
    wrong_slot = _link(2002, 10, 100, MIX, 1001)                  # game FL into AUX1
    obs = _link(2003, 10, 100, 55, 5500)                          # OBS reading the same monitor
    stp.ensure_stream_mix_links(_graph(extra=[ok_link, stray_into_input, wrong_slot, obs]))
    assert _deleted(pwlink) == {(7700, 1005), (100, 1001)}
    assert (100, 5500) not in _deleted(pwlink)
    assert (100, 1000) not in _created(pwlink)                    # already correct, untouched


def test_links_false_when_mix_node_absent(pwlink):
    assert stp.ensure_stream_mix_links([_node(10, "Arctis_Game")]) is False
    assert pwlink == []


def test_links_false_on_pw_link_failure(monkeypatch):
    monkeypatch.setattr(pw_utils.subprocess, "run",
                        lambda argv, **kw: types.SimpleNamespace(returncode=1, stderr=b"boom"))
    assert stp.ensure_stream_mix_links(_graph()) is False


def test_links_permission_refusal_grants_and_retries(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        denied = len([c for c in calls if _is_link(c)]) == 1
        return types.SimpleNamespace(returncode=1 if denied else 0,
                                     stderr=b"Operation not permitted" if denied else b"")

    monkeypatch.setattr(pw_utils.subprocess, "run", fake_run)
    with patch("arctis_sound_manager.pw_utils.grant_link_permissions", return_value=True) as grant:
        assert stp.ensure_stream_mix_links(_graph(game=("FL",), chat=("FL",), media=("FL",),
                                                  mic=False)) is True
    grant.assert_called_once()


# ── setting ──────────────────────────────────────────────────────────────────

def test_streamer_mode_defaults_off_and_roundtrips(tmp_path):
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings()
        assert gs.streamer_mode is False
        gs.streamer_mode = True
        gs.write_to_file()
        assert GeneralSettings.read_from_file().streamer_mode is True


_set_setting = ArctisManagerDbusSettingsService.set_setting.__dict__["__DBUS_METHOD"].fn


def _svc(tmp_path):
    svc = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    svc.core_engine = MagicMock()
    svc.core_engine.oled_manager = None
    svc.logger = logging.getLogger("test_stream_mix")
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        svc.core_engine.general_settings = GeneralSettings()
    return svc


@pytest.mark.parametrize("bad", [1, "true", None, [True]])
def test_set_setting_streamer_mode_rejects_non_bool(tmp_path, bad):
    svc = _svc(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread") as thread:
        assert _set_setting(svc, "streamer_mode", json.dumps(bad)) is False
    assert svc.core_engine.general_settings.streamer_mode is False
    thread.assert_not_called()


@pytest.mark.parametrize("value", [True, False])
def test_set_setting_streamer_mode_applies_off_the_dbus_loop(tmp_path, value):
    svc = _svc(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread") as thread:
        assert _set_setting(svc, "streamer_mode", json.dumps(value)) is True
    assert svc.core_engine.general_settings.streamer_mode is value
    assert thread.call_args.kwargs["target"] is svc.core_engine.apply_streamer_mode
    thread.return_value.start.assert_called_once()
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        assert GeneralSettings.read_from_file().streamer_mode is value
