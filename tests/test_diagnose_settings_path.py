# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The diagnostic report has to read the settings file that actually exists.

``GeneralSettings`` reads and writes ``SETTINGS_FOLDER/general_settings.yaml``.
The report looked one directory above that, at a path nothing has ever written,
so every report filed on an issue announced "no settings file" — and no issue
has ever shown us what the reporter had actually configured. A report that
quietly omits the one thing it was asked to collect is worse than no report:
it is read as evidence of a clean configuration.
"""
from __future__ import annotations

from unittest.mock import patch


def _write_settings(folder, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "general_settings.yaml").write_text(text)


def test_the_report_finds_the_settings_file_where_it_is_written(tmp_path):
    from arctis_sound_manager import diagnose

    settings_folder = tmp_path / "arctis_manager" / "settings"
    _write_settings(settings_folder, "external_output_device: alsa_output.test-speakers\n")

    with patch.object(diagnose, "SETTINGS_FOLDER", settings_folder):
        section = diagnose._section_settings()

    assert "no settings file" not in section
    assert "external_output_device" in section


def test_the_old_path_is_not_consulted(tmp_path):
    """A file at the previous location must not be reported as the settings.

    Directly pins the off-by-one: with a file present only at the parent, the
    report has nothing to show and must say so, rather than picking up a stray.
    """
    from arctis_sound_manager import diagnose

    settings_folder = tmp_path / "arctis_manager" / "settings"
    settings_folder.mkdir(parents=True, exist_ok=True)
    _write_settings(settings_folder.parent, "external_output_device: wrong-place\n")

    with patch.object(diagnose, "SETTINGS_FOLDER", settings_folder):
        section = diagnose._section_settings()

    assert "wrong-place" not in section


def test_a_genuinely_absent_file_still_says_so(tmp_path):
    from arctis_sound_manager import diagnose

    settings_folder = tmp_path / "arctis_manager" / "settings"
    settings_folder.mkdir(parents=True, exist_ok=True)

    with patch.object(diagnose, "SETTINGS_FOLDER", settings_folder):
        section = diagnose._section_settings()

    assert "no settings file" in section
