#!/usr/bin/env python3
"""Generate the standard object/field label map used by the translation CSV step.

Standard object labels (singular/plural) and standard field labels are not in local
metadata, so this describes the referenced standard objects in the org and writes
standard_labels.json:

  {
    "objects": { "Account": {"label": "Account", "labelPlural": "Accounts"}, ... },
    "fields":  { "Account.Type": "Type", "Account.AccountNumber": "Account Number", ... }
  }

Only standard objects (no namespace suffix) and the standard (non-custom) fields referenced
in the input CSV are included.

NOTE: This is OPTIONAL and only used by `to-csv --include-unchanged-standard-fields`.
By default, standard field/object labels that need translation are the RENAMED ones, which
`to-csv` reads directly from the org default-language (en_US) CustomObjectTranslation in
local metadata -- no describe needed. Run this only if you also want to translate standard
field labels that have NOT been renamed (usually unnecessary; Salesforce auto-translates
those).

Needs org access; reads only (never deploys).

Examples:
  python3 scripts/python/translation-helpers/gen_standard_labels.py ac_fields.csv --target-org <your-org>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

CUSTOM_SUFFIX = re.compile(r"__[a-zA-Z]+$")
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]


def referenced(csv_path: Path) -> tuple[set[str], set[str]]:
    """Return (standard objects, standard Object.Field names) referenced in the CSV."""
    objects: set[str] = set()
    fields: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        name_idx = header.index("Name") if header and "Name" in header else 0
        for row in reader:
            if not row or "." not in row[name_idx]:
                continue
            obj, field = row[name_idx].split(".", 1)
            if CUSTOM_SUFFIX.search(obj):  # custom object -> local metadata covers it
                continue
            objects.add(obj)
            if not field.endswith("__c"):  # standard field
                fields.add(f"{obj}.{field}")
    return objects, fields


def describe(obj: str, target_org: str) -> dict | None:
    result = subprocess.run(
        _SF + ["sobject", "describe", "-s", obj, "-o", target_org, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("result")
    except json.JSONDecodeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Describe referenced standard objects and emit their object/field "
        "labels as standard_labels.json for the translation CSV step.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_file", metavar="FILE",
                        help="CSV in ac_fields.csv format (Name = Object.Field).")
    parser.add_argument("--target-org", required=True,
                        help="Org alias to describe against (required).")
    parser.add_argument("-o", "--output", type=Path, default=Path("standard_labels.json"),
                        help="Output JSON (default: standard_labels.json).")
    args = parser.parse_args()

    objects, fields = referenced(Path(args.input_file))
    if not objects:
        sys.exit("error: no standard objects referenced in the input")

    total = len(objects)
    print(f"Describing {total} standard object(s) against {args.target_org}...", file=sys.stderr)

    out_objects: dict[str, dict] = {}
    out_fields: dict[str, str] = {}
    for i, obj in enumerate(sorted(objects), 1):
        print(f"  [{i}/{total}] {obj} ... ", end="", flush=True, file=sys.stderr)
        result = describe(obj, args.target_org)
        if result is None:
            print("FAILED (describe error)", file=sys.stderr)
            continue
        out_objects[obj] = {
            "label": result.get("label") or obj,
            "labelPlural": result.get("labelPlural") or obj,
        }
        n = 0
        # Emit ALL standard (non-custom) field labels, not just the referenced ones, so the
        # map is complete enough to reverse-resolve rename tokens -> Object.Field in to-csv.
        for f in result.get("fields", []):
            if not f.get("custom") and f.get("label"):
                out_fields[f"{obj}.{f['name']}"] = f["label"]
                n += 1
        print(f"{n} standard field label(s)", file=sys.stderr)

    payload = {
        "objects": dict(sorted(out_objects.items())),
        "fields": dict(sorted(out_fields.items())),
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    print(
        f"Wrote {len(out_objects)} object label(s) and {len(out_fields)} standard field "
        f"label(s) to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
