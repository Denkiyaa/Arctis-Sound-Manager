# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for generate_appstream_catalog — the file that makes ASM visible in
Discover and GNOME Software.
"""

import gzip
import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "generate_appstream_catalog.py"

spec = importlib.util.spec_from_file_location("gen_catalog", SCRIPT)
gen_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_catalog)


METAINFO_MIN = """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>com.github.loteran.arctis-sound-manager</id>
  <name>Arctis Sound Manager</name>
</component>
"""


def test_the_catalog_names_the_package():
    """The whole reason this file exists: metainfo alone carries no package name,
    so a software centre has nothing to install and shows nothing."""
    xml = gen_catalog.build_catalog(METAINFO_MIN)
    root = ET.fromstring(xml)

    assert root.tag == "components"
    assert root.get("origin") == "arctis-sound-manager"
    assert root.find("./component/pkgname").text == "arctis-sound-manager"


def test_the_component_identity_is_preserved():
    """A catalog whose id drifts from the metainfo's describes a different app."""
    root = ET.fromstring(gen_catalog.build_catalog(METAINFO_MIN))
    assert root.find("./component/id").text == "com.github.loteran.arctis-sound-manager"
    assert root.find("./component/name").text == "Arctis Sound Manager"


def test_an_existing_pkgname_is_not_duplicated():
    metainfo = METAINFO_MIN.replace(
        "</id>", "</id>\n  <pkgname>arctis-sound-manager</pkgname>")
    root = ET.fromstring(gen_catalog.build_catalog(metainfo))
    assert len(root.findall("./component/pkgname")) == 1


def test_metainfo_without_an_id_is_refused():
    """Better to fail the build than to ship a catalog nothing can match."""
    with pytest.raises(SystemExit):
        gen_catalog.build_catalog("<component type='desktop-application'/>")


def test_the_real_metainfo_produces_a_valid_catalog(tmp_path):
    """Run the script as packaging does, against the metainfo actually shipped."""
    out = tmp_path / "catalog.xml.gz"
    subprocess.run([sys.executable, str(SCRIPT), "--output", str(out)],
                   check=True, cwd=REPO, capture_output=True)

    root = ET.fromstring(gzip.decompress(out.read_bytes()).decode("utf-8"))
    assert root.find("./component/pkgname").text == "arctis-sound-manager"
    assert root.find("./component/id").text == "com.github.loteran.arctis-sound-manager"
