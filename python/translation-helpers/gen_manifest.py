#!/usr/bin/env python3
"""Generate the retrieve manifest (package.xml) the other scripts depend on.

Instead of assuming a hand-maintained `manifest/package.xml` exists, this builds one for your
org and field list. It combines:

  - Wildcard types that need no enumeration: CustomObject (`*`, all custom objects),
    GlobalValueSet (`*`), CustomLabels, PermissionSet (`*`), PermissionSetGroup (`*`),
    MutingPermissionSet (`*`).
  - The STANDARD objects referenced in your field CSV, added as explicit CustomObject members
    (custom objects are already covered by `*`). Task/Event also pull in `Activity`.
  - StandardValueSet members, listed from the org (the type has no wildcard).
  - The default-language (`en_US`) CustomObjectTranslation for each referenced standard object,
    listed from the org. These carry RENAMED standard field/object labels.

Retrieve with the generated file (never deploy):
  sf project retrieve start -x manifest/package.xml -o <org>

Note: page Layouts are scoped separately (see gen_layout_list.py); they are not added here.

Needs org access for the two `sf org list metadata` lookups; the rest is offline.

Examples:
  python3 scripts/python/translation-helpers/gen_manifest.py ac_fields.csv --target-org <your-org>
  python3 scripts/python/translation-helpers/gen_manifest.py ac_fields.csv --target-org <your-org> -o manifest/package.xml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

# Reuse the shared field-CSV reader so the object scope matches the other scripts exactly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from to_translation import read_fields_csv  # noqa: E402

NS = "http://soap.sforce.com/2006/04/metadata"
# Task/Event field & translation metadata lives under the shared Activity object.
ACTIVITY_ALIASES = {"Task", "Event"}
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]


def referenced_objects(csv_path: Path) -> set[str]:
    """Distinct object API names (custom + standard) from an ac_fields.csv-style CSV."""
    return {f.split(".", 1)[0] for f in read_fields_csv(csv_path) if "." in f}


def standard_objects(objects: set[str]) -> list[str]:
    """Standard objects among `objects` (no `__` namespace), with Activity for Task/Event."""
    std = {o for o in objects if "__" not in o}
    if std & ACTIVITY_ALIASES:
        std.add("Activity")
    return sorted(std)


def list_metadata(target_org: str, metadata_type: str) -> list[str]:
    """Return every fullName of a metadata type in the org via `sf org list metadata`."""
    result = subprocess.run(
        _SF + ["org", "list", "metadata", "-m", metadata_type, "-o", target_org, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"error: `sf org list metadata -m {metadata_type}` failed:\n"
                 f"{result.stderr.strip() or result.stdout.strip()}")
    payload = json.loads(result.stdout).get("result", [])
    if isinstance(payload, dict):  # a single result is returned unwrapped
        payload = [payload]
    return [item["fullName"] for item in payload if item.get("fullName")]


def en_us_translation_members(full_names: list[str], std_objects: list[str]) -> list[str]:
    """Keep `<Object>-en_US` CustomObjectTranslation names for the referenced standard objects.

    Object API names contain no '-', so the object is everything before the first '-' and the
    language is the remainder.
    """
    wanted = set(std_objects)
    out = []
    for fn in full_names:
        if "-" not in fn:
            continue
        obj, lang = fn.split("-", 1)
        if lang == "en_US" and obj in wanted:
            out.append(fn)
    return sorted(set(out))


def build_manifest(types: list[tuple[str, list[str]]], version: str) -> str:
    """Build a package.xml from an ordered list of (typeName, members). Empty types skipped."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Package xmlns="{NS}">']
    for name, members in types:
        if not members:
            continue
        lines.append("    <types>")
        for m in members:
            lines.append(f"        <members>{escape(m)}</members>")
        lines.append(f"        <name>{escape(name)}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{escape(version)}</version>")
    lines.append("</Package>")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the retrieve manifest (package.xml) the translation / permission "
        "scripts depend on, scoped to your field list and listed from the org.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_file", metavar="FILE",
                        help="CSV in ac_fields.csv format (Name = Object.Field).")
    parser.add_argument("--target-org", required=True,
                        help="Org alias to list metadata from (required).")
    parser.add_argument("-o", "--output", type=Path, default=Path("manifest/package.xml"),
                        help="Output manifest (default: manifest/package.xml).")
    parser.add_argument("--api-version", default="62.0",
                        help="API version for the generated manifest (default: 62.0).")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        sys.exit(f"error: input file not found: {input_path}")

    objects = referenced_objects(input_path)
    if not objects:
        sys.exit("error: no Object.Field rows found in the input")
    std_objects = standard_objects(objects)

    print(f"Listing standard value sets and en_US translations from {args.target_org}...",
          file=sys.stderr)
    standard_value_sets = sorted(list_metadata(args.target_org, "StandardValueSet"))
    en_us = en_us_translation_members(
        list_metadata(args.target_org, "CustomObjectTranslation"), std_objects)

    types: list[tuple[str, list[str]]] = [
        ("CustomObject", ["*"] + std_objects),  # * = all custom objects; standard listed
        ("CustomObjectTranslation", en_us),
        ("GlobalValueSet", ["*"]),
        ("CustomLabels", ["CustomLabels"]),
        ("StandardValueSet", standard_value_sets),
        ("PermissionSet", ["*"]),
        ("PermissionSetGroup", ["*"]),
        ("MutingPermissionSet", ["*"]),
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_manifest(types, args.api_version), encoding="utf-8")
    print(
        f"Wrote {args.output}: {len(std_objects)} standard object(s), {len(en_us)} en_US "
        f"translation(s), {len(standard_value_sets)} standard value set(s), plus wildcard "
        f"types. Retrieve with:\n"
        f"  sf project retrieve start -x {args.output} -o {args.target_org}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
