#!/usr/bin/env python3
"""Enumerate layouts for the referenced objects and emit a retrieve manifest.

Layout section headings are translatable (see `translation_roundtrip.py to-csv`), but the
`Layout` metadata type can't be wildcard-scoped by object in a static `package.xml`, and
layouts are not retrieved by `manifest/package.xml`. This helper bridges that gap: it lists
every Layout in the org, keeps only those belonging to the objects referenced in the input
CSV (layout fullNames are `<Object>-<Layout Name>`), and writes a small `package.xml` you can
retrieve with:

  sf project retrieve start -x layouts-package.xml -o <org>

After retrieving, `to-csv` picks up the layout sections automatically.

Needs org access; reads only (never deploys). The filtering/manifest generation is offline.

Examples:
  python3 scripts/python/translation-helpers/gen_layout_list.py ac_fields.csv --target-org <your-org>
  python3 scripts/python/translation-helpers/gen_layout_list.py ac_fields.csv -o layouts-package.xml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# Reuse the shared field-CSV reader so the object scope matches to-csv exactly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from to_translation import read_fields_csv  # noqa: E402

NS = "http://soap.sforce.com/2006/04/metadata"
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]


def referenced_objects(csv_path: Path) -> set[str]:
    """Distinct object API names (custom + standard) from an ac_fields.csv-style CSV."""
    return {f.split(".", 1)[0] for f in read_fields_csv(csv_path) if "." in f}


def list_layouts(target_org: str) -> list[str]:
    """Return every Layout fullName in the org via `sf org list metadata`."""
    result = subprocess.run(
        _SF + ["org", "list", "metadata", "-m", "Layout", "-o", target_org, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"error: `sf org list metadata -m Layout` failed:\n"
                 f"{result.stderr.strip() or result.stdout.strip()}")
    payload = json.loads(result.stdout).get("result", [])
    if isinstance(payload, dict):  # a single result is returned unwrapped
        payload = [payload]
    return [item["fullName"] for item in payload if item.get("fullName")]


def filter_layouts(full_names: list[str], objects: set[str]) -> list[str]:
    """Keep layout fullNames whose object prefix (`<Object>-...`) is in `objects`.

    Object API names contain no '-', so the object is everything before the first '-'.
    """
    kept = [fn for fn in full_names if "-" in fn and fn.split("-", 1)[0] in objects]
    return sorted(set(kept))


def build_manifest(members: list[str], version: str) -> str:
    """Build a package.xml containing just the given Layout members."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<Package xmlns="{NS}">', "    <types>"]
    for m in members:
        lines.append(f"        <members>{escape(m)}</members>")
    lines.append("        <name>Layout</name>")
    lines.append("    </types>")
    lines.append(f"    <version>{escape(version)}</version>")
    lines.append("</Package>")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List org layouts, keep those for the referenced objects, and write a "
        "package.xml to retrieve them (for layout section heading translation).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_file", metavar="FILE",
                        help="CSV in ac_fields.csv format (Name = Object.Field).")
    parser.add_argument("--target-org", required=True,
                        help="Org alias to list metadata from (required).")
    parser.add_argument("-o", "--output", type=Path, default=Path("layouts-package.xml"),
                        help="Output manifest (default: layouts-package.xml).")
    parser.add_argument("--api-version", default="62.0",
                        help="API version for the generated manifest (default: 62.0).")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        sys.exit(f"error: input file not found: {input_path}")

    objects = referenced_objects(input_path)
    if not objects:
        sys.exit("error: no Object.Field rows found in the input")

    print(f"Listing layouts in {args.target_org}...", file=sys.stderr)
    members = filter_layouts(list_layouts(args.target_org), objects)
    if not members:
        sys.exit("error: no layouts matched the referenced objects (nothing to retrieve)")

    args.output.write_text(build_manifest(members, args.api_version), encoding="utf-8")
    print(
        f"Wrote {len(members)} layout member(s) for {len(objects)} object(s) to "
        f"{args.output}. Retrieve with:\n"
        f"  sf project retrieve start -x {args.output} -o {args.target_org}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
