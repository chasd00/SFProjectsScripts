#!/usr/bin/env python3
"""Generate the standard-picklist value map used by expand_picklists.py.

Standard picklist fields (e.g. Account.Type) keep their values in a StandardValueSet whose
name is not exposed in local metadata, so they can't be resolved offline. This script asks
the org instead: it runs `sf sobject describe` on the standard objects referenced by a
field-permissions CSV and writes a JSON map of `Object.Field` -> [picklist values] for
every standard (non-custom) picklist field. expand_picklists.py loads that JSON and uses it
as the source of truth for standard picklist fields, falling back to its built-in curated
map when the JSON is absent.

Re-run this whenever the org's standard picklists change. It needs org access; the expand
step itself stays offline by reading the generated JSON.

Examples:
  python3 scripts/python/translation-helpers/gen_standard_picklists.py all_fields.csv
  python3 scripts/python/translation-helpers/gen_standard_picklists.py fields.csv --target-org <your-org> -o standard_picklists.json
  python3 scripts/python/translation-helpers/gen_standard_picklists.py --objects Account,Contact,Task
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

PICKLIST_TYPES = {"picklist", "multipicklist"}
# Object/field names ending in a custom suffix (__c, __r, ...) are not standard.
CUSTOM_SUFFIX = re.compile(r"__[a-zA-Z]+$")
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]
# Task/Event picklists are described on those objects directly (the Activity object is not
# directly describable), so no special handling is needed here.


def standard_objects_from_csv(csv_path: Path) -> list[str]:
    """Return the distinct standard objects referenced in a field-permissions CSV."""
    objs: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        name_idx = header.index("Name") if header and "Name" in header else 0
        for row in reader:
            if not row or "." not in row[name_idx]:
                continue
            obj = row[name_idx].split(".", 1)[0]
            if not CUSTOM_SUFFIX.search(obj):  # standard object
                objs.add(obj)
    return sorted(objs)


def describe(obj: str, target_org: str) -> dict | None:
    """Run `sf sobject describe` for one object; return parsed JSON result or None."""
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
        description="Generate the Object.Field -> [values] map of standard picklist "
        "fields by describing referenced standard objects in the org.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input_file",
        metavar="FILE",
        nargs="?",
        help="Field-permissions CSV used to scope which standard objects to describe. "
        "Omit if you pass --objects.",
    )
    parser.add_argument(
        "--objects",
        help="Comma-separated standard object list to describe instead of scoping from a CSV.",
    )
    parser.add_argument(
        "--target-org",
        required=True,
        help="Org alias to describe against (required).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="standard_picklists.json",
        help="Output JSON map (default: standard_picklists.json).",
    )
    args = parser.parse_args()

    if args.objects:
        objects = [o.strip() for o in args.objects.split(",") if o.strip()]
    elif args.input_file:
        objects = standard_objects_from_csv(Path(args.input_file))
    else:
        parser.error("provide a field-permissions CSV or --objects")

    if not objects:
        sys.exit("error: no standard objects to describe")

    total = len(objects)
    print(f"Describing {total} standard object(s) against {args.target_org}...", file=sys.stderr)

    mapping: dict[str, list[str]] = {}
    field_count = 0
    for i, obj in enumerate(objects, 1):
        print(f"  [{i}/{total}] {obj} ... ", end="", flush=True, file=sys.stderr)
        result = describe(obj, args.target_org)
        if result is None:
            print("FAILED (describe error)", file=sys.stderr)
            continue
        fields = result.get("fields", [])
        n = 0
        for f in fields:
            if f.get("type") not in PICKLIST_TYPES or f.get("custom"):
                continue  # custom picklists resolve from local metadata, not here
            values = [v["value"] for v in f.get("picklistValues", []) if v.get("value")]
            if not values:
                continue
            mapping[f"{obj}.{f['name']}"] = values
            n += 1
        field_count += n
        print(f"{n} standard picklist field(s)", file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(mapping.items())), fh, indent=2)
        fh.write("\n")

    print(
        f"Wrote {field_count} standard picklist field(s) across {total} object(s) "
        f"to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
