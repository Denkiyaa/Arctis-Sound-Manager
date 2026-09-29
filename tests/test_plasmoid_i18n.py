# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Plasma widget's strings: [plasmoid] in lang/*.ini, turned into
strings.js at build time by scripts/generate_plasmoid_i18n.py.

A key the QML asks for but en.ini lacks shows up as the raw key in the
widget, and no Crowdin translator ever sees it.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI_DIR = ROOT / 'src' / 'arctis_sound_manager' / 'desktop' / 'plasmoid' / 'contents' / 'ui'


def _generator():
    spec = importlib.util.spec_from_file_location(
        'generate_plasmoid_i18n', ROOT / 'scripts' / 'generate_plasmoid_i18n.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_key_the_widget_uses_is_in_english():
    used = set()
    for qml in UI_DIR.glob('*.qml'):
        used |= set(re.findall(r'\btr\("([a-z0-9_]+)"', qml.read_text(encoding='utf-8')))
    assert used, 'no tr("…") call found — did the helper get renamed?'
    english = _generator().collect()['en']
    assert not used - english.keys(), sorted(used - english.keys())


def test_only_the_plasmoid_section_is_exported(tmp_path):
    (tmp_path / 'en.ini').write_text(
        '[ui]\nopen_app = Open\n\n[plasmoid]\nheadset = Headset\n', encoding='utf-8')
    (tmp_path / 'fr.ini').write_text(
        '[plasmoid]\nheadset=Casque\nbattery=\n', encoding='utf-8')
    (tmp_path / 'de.ini').write_text('[ui]\nopen_app=Öffnen\n', encoding='utf-8')
    # Empty values are left out so the widget falls back to English instead
    # of showing a blank label; a language without the section is left out
    # entirely.
    assert _generator().collect(tmp_path) == {
        'en': {'headset': 'Headset'},
        'fr': {'headset': 'Casque'},
    }


def test_a_broken_translation_does_not_fail_the_build(tmp_path):
    (tmp_path / 'en.ini').write_text('[plasmoid]\nheadset = Headset\n', encoding='utf-8')
    (tmp_path / 'xx.ini').write_text('[plasmoid]\nheadset=a\nheadset=b\n', encoding='utf-8')
    assert _generator().collect(tmp_path) == {'en': {'headset': 'Headset'}}


def test_output_is_a_qml_js_library():
    text = _generator().render({'en': {'headset': 'Headset'}})
    assert '.pragma library' in text
    assert 'var strings = {' in text
