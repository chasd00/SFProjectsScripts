#!/usr/bin/env python3
"""Expand picklist fields in a psg2csv field-permissions CSV into one row per value.

Reads a combined field-permissions CSV (see permissions-helper/psg2csv_collect.py, run with
`-r fieldPermissions`) whose Name column holds `Object.Field` entries. For each row it
looks up the field's metadata under force-app/main/default/objects/<Object>/fields/. If
the field is a Picklist or MultiselectPicklist, the row is expanded into one row per
picklist value, with the value recorded in a new `PicklistValue` column. Picklist values
are resolved from any of three sources: an inline value set (valueSetDefinition), a global
value set (valueSetName -> globalValueSets/<name>.globalValueSet-meta.xml), or -- for
standard picklist fields such as Account.Type that have no field metadata -- the standard
picklist map. For standard fields the generated standard_picklists.json (produced by
gen_standard_picklists.py from `sf sobject describe`) is preferred; when it is absent the
script falls back to a built-in curated Object.Field -> StandardValueSet map resolved
against standardValueSets/.

Non-picklist field rows -- and rows whose Name is not `Object.Field` (e.g. object or user
permission output) -- pass through unchanged with an empty PicklistValue.

This reads local project metadata only -- it never deploys to the org.

Examples:
  python3 scripts/python/expand_picklists.py psg_combined.csv
  python3 scripts/python/expand_picklists.py fields.csv -o fields_expanded.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PICKLIST_TYPES = {"Picklist", "MultiselectPicklist"}

# Custom fields on Task and Event are shared via the special Activity object, so their
# field metadata is stored under objects/Activity/fields/ rather than Task/ or Event/.
ACTIVITY_ALIASES = {"Task", "Event"}

# Standard picklist fields have no field-meta.xml; their values live in a StandardValueSet
# whose name is not derivable from the field name. This curated map covers the standard
# picklist fields referenced in this project, keyed by "Object.Field" -> StandardValueSet.
# Several fields legitimately share one value set (e.g. all *.LeadSource use LeadSource).
# Add entries here as new standard picklist fields appear; an entry whose StandardValueSet
# is not on disk simply falls through to a warning rather than erroring.
STANDARD_VALUE_SETS = {
    "AccountContactRelation.Roles": "AccountContactMultiRoles",
    "Account.Type": "AccountType",
    "Account.Industry": "Industry",
    "Account.Ownership": "AccountOwnership",
    "Account.Rating": "AccountRating",
    "Account.AccountSource": "LeadSource",
    "Asset.Status": "AssetStatus",
    "Asset.ProductFamily": "Product2Family",
    "Campaign.Status": "CampaignStatus",
    "Campaign.Type": "CampaignType",
    "Case.Origin": "CaseOrigin",
    "Case.Priority": "CasePriority",
    "Case.Reason": "CaseReason",
    "Case.Status": "CaseStatus",
    "Case.Type": "CaseType",
    "Contact.LeadSource": "LeadSource",
    "Contact.Salutation": "Salutation",
    "Contract.Status": "ContractStatus",
    "Entitlement.Type": "EntitlementType",
    "Event.Type": "EventType",
    "Lead.Industry": "Industry",
    "Lead.LeadSource": "LeadSource",
    "Lead.Salutation": "Salutation",
    "Lead.Status": "LeadStatus",
    "Opportunity.LeadSource": "LeadSource",
    "Opportunity.StageName": "OpportunityStage",
    "Opportunity.Type": "OpportunityType",
    "Order.Status": "OrderStatus",
    "Order.Type": "OrderType",
    "Product2.Family": "Product2Family",
    "Task.Priority": "TaskPriority",
    "Task.Status": "TaskStatus",
    "Task.Type": "TaskType",
}


def find_default_dir(projectdir: Path | None) -> Path:
    """Locate the force-app/main/default directory that holds objects/ and globalValueSets/."""
    base = projectdir or Path.cwd()
    candidate = base / "force-app" / "main" / "default"
    if (candidate / "objects").is_dir():
        return candidate
    for objects_dir in base.rglob("objects"):
        if objects_dir.is_dir() and objects_dir.name == "objects":
            return objects_dir.parent
    sys.exit(f"error: could not find a force-app/main/default directory under {base}")


def load_global_value_set(
    default_dir: Path, name: str, cache: dict[str, list[str] | None]
) -> list[str] | None:
    """Return the value fullNames of a global value set, or None if it is missing."""
    if name in cache:
        return cache[name]
    file = default_dir / "globalValueSets" / f"{name}.globalValueSet-meta.xml"
    if not file.is_file():
        cache[name] = None
        return None
    root = ET.parse(file).getroot()
    values = [cv.findtext("{*}fullName") for cv in root.findall("{*}customValue")]
    values = [v for v in values if v]
    cache[name] = values
    return values


def load_standard_value_set(
    default_dir: Path, name: str, cache: dict[str, list[str] | None]
) -> list[str] | None:
    """Return the value fullNames of a standard value set, or None if it is missing."""
    if name in cache:
        return cache[name]
    file = default_dir / "standardValueSets" / f"{name}.standardValueSet-meta.xml"
    if not file.is_file():
        cache[name] = None
        return None
    root = ET.parse(file).getroot()
    values = [sv.findtext("{*}fullName") for sv in root.findall("{*}standardValue")]
    values = [v for v in values if v]
    cache[name] = values
    return values


def resolve_standard(
    default_dir: Path,
    obj: str,
    field: str,
    svs_cache: dict[str, list[str] | None],
    generated: dict[str, list[str]] | None,
) -> tuple[list[str] | None, str | None]:
    """Resolve a standard picklist field's values.

    Prefers the generated map (gen_standard_picklists.py, describe-sourced) and falls back
    to the curated Object.Field -> StandardValueSet map. Returns (values, note); values is
    None when the field is in neither source.
    """
    key = f"{obj}.{field}"
    if generated and key in generated:
        return generated[key], None
    svs_name = STANDARD_VALUE_SETS.get(key)
    if not svs_name:
        return None, None
    values = load_standard_value_set(default_dir, svs_name, svs_cache)
    if values is None:
        return [], f"{obj}.{field} maps to missing StandardValueSet {svs_name}"
    return values, None


def field_picklist_values(
    default_dir: Path,
    obj: str,
    field: str,
    gvs_cache: dict[str, list[str] | None],
    svs_cache: dict[str, list[str] | None],
    generated: dict[str, list[str]] | None,
) -> tuple[list[str] | None, str | None]:
    """Resolve picklist values for Object.Field.

    Returns (values, note). values is None when the field is not a picklist (or its
    metadata is absent); a list (possibly empty) when it is a picklist. note carries a
    human-readable reason for anything unexpected, for warnings.
    """
    field_file = default_dir / "objects" / obj / "fields" / f"{field}.field-meta.xml"
    if not field_file.is_file() and obj in ACTIVITY_ALIASES:
        # Task/Event custom fields live under the shared Activity object.
        field_file = default_dir / "objects" / "Activity" / "fields" / f"{field}.field-meta.xml"
    if not field_file.is_file():
        # Uncustomized standard fields have no field metadata at all; if it is a known
        # standard picklist field, resolve it from the generated/curated standard map.
        values, note = resolve_standard(default_dir, obj, field, svs_cache, generated)
        if values is not None:
            return values, note
        return None, f"no field metadata for {obj}.{field}"
    root = ET.parse(field_file).getroot()
    ftype = root.findtext("{*}type")
    if ftype not in PICKLIST_TYPES:
        return None, None  # not a picklist -- nothing to expand

    value_set = root.find("{*}valueSet")
    if value_set is None:
        # A picklist field with no inline/global value set (typically a customized
        # standard field) draws its values from a StandardValueSet.
        values, note = resolve_standard(default_dir, obj, field, svs_cache, generated)
        if values is not None:
            return values, note
        return [], f"{obj}.{field} is {ftype} but has no valueSet (no standard mapping)"

    global_name = value_set.findtext("{*}valueSetName")
    if global_name:
        values = load_global_value_set(default_dir, global_name, gvs_cache)
        if values is None:
            return [], f"{obj}.{field} references missing global value set {global_name}"
        return values, None

    definition = value_set.find("{*}valueSetDefinition")
    if definition is not None:
        values = [v.findtext("{*}fullName") for v in definition.findall("{*}value")]
        return [v for v in values if v], None

    return [], f"{obj}.{field} valueSet not understood"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand picklist fields in a psg2csv field-permissions CSV into one "
        "row per picklist value.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input_file",
        metavar="FILE",
        help="The field-permissions CSV to process (from permissions-helper/psg2csv_collect.py "
        "run with -r fieldPermissions). Its Name column must hold Object.Field entries.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output CSV (default: <input stem>_expanded.csv).",
    )
    parser.add_argument(
        "-d",
        "--projectdir",
        type=Path,
        default=None,
        help="Project root if not the current directory.",
    )
    parser.add_argument(
        "--standard-picklists",
        type=Path,
        default=Path("standard_picklists.json"),
        help="JSON map of standard picklist fields from gen_standard_picklists.py "
        "(default: standard_picklists.json). Used in preference to the built-in curated "
        "map; ignored if the file is absent.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        sys.exit(f"error: input file not found: {input_path}")
    output_path = Path(args.output) if args.output else input_path.with_name(
        f"{input_path.stem}_expanded{input_path.suffix or '.csv'}"
    )

    default_dir = find_default_dir(args.projectdir)

    generated: dict[str, list[str]] | None = None
    if args.standard_picklists.is_file():
        with open(args.standard_picklists, encoding="utf-8") as fh:
            generated = json.load(fh)
        print(
            f"Loaded {len(generated)} standard picklist field(s) from "
            f"{args.standard_picklists}.",
            file=sys.stderr,
        )

    with open(input_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            sys.exit("error: input CSV is empty")
        rows = list(reader)

    name_idx = header.index("Name") if "Name" in header else 0
    # Add the PicklistValue column right after Name.
    out_header = header[: name_idx + 1] + ["PicklistValue"] + header[name_idx + 1 :]

    gvs_cache: dict[str, list[str] | None] = {}
    svs_cache: dict[str, list[str] | None] = {}
    out_rows: list[list[str]] = []
    expanded_fields = 0
    added_rows = 0

    total = len(rows)
    print(f"Expanding picklists across {total} field row(s)...", file=sys.stderr)

    def emit(row: list[str], value: str) -> None:
        out_rows.append(row[: name_idx + 1] + [value] + row[name_idx + 1 :])

    for processed, row in enumerate(rows, 1):
        if processed % 200 == 0 or processed == total:
            print(f"  [{processed}/{total}] rows processed", file=sys.stderr)
        if not row:
            continue
        name = row[name_idx]
        if "." not in name:
            emit(row, "")  # object/user permission rows have no Object.Field
            continue
        obj, field = name.split(".", 1)
        values, note = field_picklist_values(
            default_dir, obj, field, gvs_cache, svs_cache, generated
        )
        if values is None:
            if note:  # missing metadata, etc. -- keep the row, warn
                sys.stderr.write(f"warning: {note}\n")
            emit(row, "")
            continue
        if not values:  # picklist but no resolvable values
            if note:
                sys.stderr.write(f"warning: {note}\n")
            emit(row, "")
            continue
        for value in values:
            emit(row, value)
        expanded_fields += 1
        added_rows += len(values) - 1

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(out_header)
        writer.writerows(out_rows)

    print(
        f"Wrote {len(out_rows)} row(s) to {output_path} "
        f"({expanded_fields} picklist field(s) expanded, {added_rows} row(s) added).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
