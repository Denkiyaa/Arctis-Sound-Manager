"""ChatMix OSD: when the floating bar comes up, and where it points."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from arctis_sound_manager.gui import chatmix_osd
from arctis_sound_manager.gui.home_page import chatmix_bar_position


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _levels(game, chat, media, aux=None):
    return {"game": game, "chat": chat, "media": media, "aux": aux}


class _FakeOsd:
    def __init__(self):
        self.shown = []

    def show_position(self, position, channels):
        self.shown.append((position, list(channels)))


@pytest.fixture
def watcher(qapp, monkeypatch):
    state = {"enabled": True, "channels": ["game"]}
    w = chatmix_osd.ChatMixWatcher(
        lambda: True, lambda: state["enabled"], lambda: state["channels"],
    )
    w._osd = _FakeOsd()
    monkeypatch.setattr(chatmix_osd.QApplication, "activeWindow", staticmethod(lambda: None))
    w.state = state
    return w


def test_bar_position_matches_the_mixer_page():
    # Chat pulled down to 40 with Game full → left of centre.
    assert chatmix_bar_position({"game": 100}, 40, ["game"]) == 20
    # Several channels ride together as their average.
    assert chatmix_bar_position({"game": 100, "media": 60}, 100, ["game", "media"]) == 60
    # Nothing to place the bar from.
    assert chatmix_bar_position({"game": None}, 100, ["game"]) is None
    assert chatmix_bar_position({"game": 100}, None, ["game"]) is None


def test_first_reading_is_only_a_baseline(watcher):
    watcher._on_volumes(_levels(100, 40, 100))
    assert watcher._osd.shown == []


def test_shows_when_the_mix_moves(watcher):
    watcher._on_volumes(_levels(100, 40, 100))
    watcher._on_volumes(_levels(100, 60, 100))
    assert watcher._osd.shown == [(30, ["game"])]


def test_plain_volume_change_off_the_mix_is_ignored(watcher):
    watcher._on_volumes(_levels(100, 100, 100))
    # Media is not on the bar: its volume moving leaves the position alone.
    watcher._on_volumes(_levels(100, 100, 30))
    assert watcher._osd.shown == []


def test_reconnect_resets_the_baseline(watcher):
    watcher._on_volumes(_levels(100, 40, 100))
    watcher._on_volumes(None)  # PipeWire came back
    watcher._on_volumes(_levels(100, 80, 100))
    assert watcher._osd.shown == []


def test_disabled_setting_keeps_it_hidden(watcher):
    watcher.state["enabled"] = False
    watcher._on_volumes(_levels(100, 40, 100))
    watcher._on_volumes(_levels(100, 60, 100))
    assert watcher._osd.shown == []


def test_hidden_while_an_asm_window_has_focus(watcher, monkeypatch):
    monkeypatch.setattr(chatmix_osd.QApplication, "activeWindow", staticmethod(lambda: object()))
    watcher._on_volumes(_levels(100, 40, 100))
    watcher._on_volumes(_levels(100, 60, 100))
    assert watcher._osd.shown == []


def test_layer_shell_refused_next_to_a_foreign_qtcore(monkeypatch, tmp_path):
    # A PySide6 wheel's bundled Qt: no LayerShellQt beside it → never load
    # the system one into the process.
    monkeypatch.setattr(chatmix_osd, "_loaded_qtcore_dir", lambda: tmp_path)
    assert chatmix_osd._load_layer_shell() is None


# ── GNOME Wayland: the bar goes to the Shell extension ─────────────────────

def test_payload_carries_the_mixer_page_colours():
    style = chatmix_osd.osd_style(["game", "media"])
    assert len(style["left_colors"]) == 2
    assert style["chat_color"].startswith("#")
    assert {"left_label", "right_label", "bg_color", "border_color", "text_color",
            "cap_color"} <= style.keys()


def test_gnome_falls_back_to_the_extension(qapp, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(chatmix_osd.ChatMixOsd, "usable", property(lambda self: False))
    assert isinstance(chatmix_osd._make_osd(), chatmix_osd.GnomeShellOsd)


def test_other_desktops_keep_the_qt_osd(qapp, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(chatmix_osd.ChatMixOsd, "usable", property(lambda self: False))
    assert isinstance(chatmix_osd._make_osd(), chatmix_osd.ChatMixOsd)


class _Gsettings:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __call__(self, cmd, **_kw):
        self.calls.append(cmd)
        if cmd[:2] == ["gsettings", "set"]:
            self.value = cmd[-1]
        return chatmix_osd.subprocess.CompletedProcess(cmd, 0, stdout=self.value)


@pytest.fixture
def gnome(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(chatmix_osd, "_gnome_extension_installed", lambda: True)
    return tmp_path


def test_extension_is_enabled_once(gnome, monkeypatch):
    run = _Gsettings("['dash-to-dock@micxgx.gmail.com']")
    monkeypatch.setattr(chatmix_osd.subprocess, "run", run)

    chatmix_osd.enable_gnome_extension_once()

    assert run.value == str(["dash-to-dock@micxgx.gmail.com", chatmix_osd.GNOME_EXTENSION_UUID])
    # The user switching it off afterwards is not overruled.
    run.value = "['dash-to-dock@micxgx.gmail.com']"
    run.calls.clear()
    chatmix_osd.enable_gnome_extension_once()
    assert run.calls == []


def test_extension_enabled_from_an_empty_list(gnome, monkeypatch):
    run = _Gsettings("@as []")
    monkeypatch.setattr(chatmix_osd.subprocess, "run", run)

    chatmix_osd.enable_gnome_extension_once()

    assert run.value == str([chatmix_osd.GNOME_EXTENSION_UUID])


def test_extension_left_alone_outside_gnome(gnome, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    run = _Gsettings("[]")
    monkeypatch.setattr(chatmix_osd.subprocess, "run", run)

    chatmix_osd.enable_gnome_extension_once()

    assert run.calls == []
