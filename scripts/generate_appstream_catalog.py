#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the AppStream *catalog* file from the metainfo file.

Why both exist, and why shipping only the metainfo is not enough:

``/usr/share/metainfo/*.metainfo.xml`` describes the application. It says
nothing about which package delivers it — it cannot, since upstream does not
know how each distribution will name it. Software centres therefore read a
second kind of file, the *catalog*, which a distribution generates for its
repositories and which carries a ``<pkgname>`` per component.

Arch's ``archlinux-appstream-data`` provides that for the official repositories
only. A package installed from anywhere else — our pacman repository, and every
third-party repository — ends up with a component AppStream knows about but
cannot connect to any package. Discover then shows nothing at all: searching
"arctis" returned an empty list on a machine where both PackageKit and the
repository were working perfectly.

So we ship our own catalog entry alongside the metainfo. The component is the
same; the catalog adds the one fact only the packaging knows.

Usage:
    python3 scripts/generate_appstream_catalog.py [--output PATH]

Reads the metainfo file from the source tree, writes gzipped catalog XML to
stdout (or to --output). Called from every packaging's install step.
"""

from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METAINFO = (REPO_ROOT / "src" / "arctis_sound_manager" / "desktop"
            / "com.github.loteran.arctis-sound-manager.metainfo.xml")

#: The distro package name. Kept here rather than passed in: all three of our
#: packagings use the same name, and a catalog naming a package that does not
#: exist is worse than no catalog at all.
PKGNAME = "arctis-sound-manager"

#: Origin identifies where the catalog data came from. It must not collide with
#: a distribution's own origins, or the two sets of metadata fight.
ORIGIN = "arctis-sound-manager"


def build_catalog(metainfo_xml: str) -> str:
    """Wrap a metainfo component into a catalog collection carrying <pkgname>."""
    body = re.sub(r"<\?xml[^>]*\?>\s*", "", metainfo_xml).strip()

    if "<pkgname>" not in body:
        # Right after <id>: AppStream does not require an order, but keeping the
        # identity fields together is what every distribution's catalog looks
        # like, and makes the generated file readable.
        body, count = re.subn(r"(</id>)", r"\1\n  <pkgname>%s</pkgname>" % PKGNAME,
                              body, count=1)
        if not count:
            raise SystemExit("metainfo has no <id> element — refusing to write a "
                             "catalog that no software centre could match")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<components version="1.0" origin="{ORIGIN}">\n'
        f"{body}\n"
        "</components>\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path,
                    help="write gzipped catalog here (default: stdout)")
    ap.add_argument("--metainfo", type=Path, default=METAINFO,
                    help="metainfo file to read (default: the one in this tree)")
    args = ap.parse_args()

    if not args.metainfo.is_file():
        raise SystemExit(f"metainfo not found: {args.metainfo}")

    catalog = build_catalog(args.metainfo.read_text(encoding="utf-8"))
    data = gzip.compress(catalog.encode("utf-8"), mtime=0)  # mtime=0: reproducible

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
