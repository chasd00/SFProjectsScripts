#!/usr/bin/env python3
"""Generate Salesforce Metadata API translation files from an ac_fields.csv-style CSV.

Input is any CSV in the ac_fields.csv format (Name = Object.Field, plus editable/readable/
PSG columns, which are ignored). For the distinct fields it produces skeleton translation
metadata for a target language, ready to be filled in by a translator/TMS and deployed via
the Metadata API. Three translation types are emitted, routed by where a field's picklist
values come from:

  - Custom field labels + inline picklist values  -> CustomObjectTranslation (decomposed)
        objectTranslations/<Object>-<lang>/<Object>-<lang>.objectTranslation-meta.xml (container)
        objectTranslations/<Object>-<lang>/<Field>.fieldTranslation-meta.xml (one per field)
  - Global value set values (deduped per value set) -> GlobalValueSetTranslation
        globalValueSetTranslations/<ValueSet>-<lang>.globalValueSetTranslation-meta.xml
  - Standard value set values (deduped per value set) -> StandardValueSetTranslation
        standardValueSetTranslations/<ValueSet>-<lang>.standardValueSetTranslation-meta.xml

Output is SFDX source format; the folder tree above is created under the current directory.

Translation slots are emitted empty (the masterLabel identifies each item; the source text
is shown in an XML comment for field labels). Standard field labels are not custom-field
translations, so they are skipped (only their values are translated).

Reads local metadata only (never deploys). Metadata is located via -d/--projectdir; output
files are always written to the current directory.

Examples:
  python3 scripts/python/to_translation.py ac_fields.csv de
  python3 scripts/python/to_translation.py fields.csv fr -d ~/proj
"""

from __future__ import annotations

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

# Reuse the field-resolution constants/helpers so routing stays consistent with expansion.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from expand_picklists import (  # noqa: E402
    ACTIVITY_ALIASES,
    PICKLIST_TYPES,
    STANDARD_VALUE_SETS,
    find_default_dir,
)

NS = "http://soap.sforce.com/2006/04/metadata"


def field_meta_path(default_dir: Path, obj: str, field: str) -> Path:
    """Path to a field's metadata, honoring the Task/Event -> Activity sharing."""
    path = default_dir / "objects" / obj / "fields" / f"{field}.field-meta.xml"
    if not path.is_file() and obj in ACTIVITY_ALIASES:
        path = default_dir / "objects" / "Activity" / "fields" / f"{field}.field-meta.xml"
    return path


def read_field(default_dir: Path, obj: str, field: str) -> dict | None:
    """Return label/type/value-set routing info for a field, or None if no metadata."""
    path = field_meta_path(default_dir, obj, field)
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    return {
        "label": root.findtext("{*}label"),
        "type": root.findtext("{*}type"),
        "has_value_set": root.find("{*}valueSet") is not None,
        "value_set_name": root.findtext("{*}valueSet/{*}valueSetName"),
        "inline": [
            (v.findtext("{*}fullName"), v.findtext("{*}label"))
            for v in root.findall("{*}valueSet/{*}valueSetDefinition/{*}value")
        ],
    }


def read_value_set_labels(file: Path, value_tag: str) -> list[str]:
    """Return the master labels of every value in a global/standard value set file."""
    if not file.is_file():
        return []
    root = ET.parse(file).getroot()
    labels: list[str] = []
    for value in root.findall(f"{{*}}{value_tag}"):
        labels.append(value.findtext("{*}label") or value.findtext("{*}fullName") or "")
    return [v for v in labels if v]


def comment(text: str) -> str:
    """A safe XML comment (no '--' sequence)."""
    return f"<!-- source: {escape(text).replace('--', '- -')} -->"


def write_object_translation(
    obj: str, fields: list[dict], lang: str, out_dir: Path
) -> list[Path]:
    """Write a decomposed (source-format) object translation.

    Creates objectTranslations/<obj>-<lang>/ with a container objectTranslation file plus
    one <Field>.fieldTranslation-meta.xml per field. Returns all paths written.
    """
    folder = out_dir / "objectTranslations" / f"{obj}-{lang}"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    container = folder / f"{obj}-{lang}.objectTranslation-meta.xml"
    container.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<CustomObjectTranslation xmlns="{NS}">\n'
        "</CustomObjectTranslation>\n",
        encoding="utf-8",
    )
    written.append(container)

    for f in sorted(fields, key=lambda x: x["name"]):
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<CustomFieldTranslation xmlns="{NS}">',
        ]
        if f["label"]:
            lines.append(f"    {comment(f['label'])}")
        lines.append("    <label></label>")
        lines.append(f"    <name>{escape(f['name'])}</name>")
        for _value, label in f["inline"]:
            master = label or _value or ""
            lines.append("    <picklistValues>")
            lines.append(f"        <masterLabel>{escape(master)}</masterLabel>")
            lines.append("        <translation></translation>")
            lines.append("    </picklistValues>")
        lines.append("</CustomFieldTranslation>")
        path = folder / f"{f['name']}.fieldTranslation-meta.xml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)

    return written


def write_value_set_translation(
    name: str, labels: list[str], lang: str, out_dir: Path, kind: str
) -> Path:
    """Write a Global/Standard ValueSetTranslation file (kind: 'global' or 'standard')."""
    root_tag = "GlobalValueSetTranslation" if kind == "global" else "StandardValueSetTranslation"
    suffix = "globalValueSetTranslation" if kind == "global" else "standardValueSetTranslation"
    subdir = "globalValueSetTranslations" if kind == "global" else "standardValueSetTranslations"
    folder = out_dir / subdir
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<{root_tag} xmlns="{NS}">',
    ]
    for label in labels:
        lines.append("    <valueTranslation>")
        lines.append(f"        <masterLabel>{escape(label)}</masterLabel>")
        lines.append("        <translation></translation>")
        lines.append("    </valueTranslation>")
    lines.append(f"</{root_tag}>")
    path = folder / f"{name}-{lang}.{suffix}-meta.xml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_fields_csv(input_path: Path) -> set[str]:
    """Return the distinct Object.Field names from an ac_fields.csv-style CSV."""
    fields: set[str] = set()
    with open(input_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        name_idx = header.index("Name") if header and "Name" in header else 0
        for row in reader:
            if row and "." in row[name_idx]:
                fields.add(row[name_idx])
    return fields


def collect(
    fields: set[str], default_dir: Path
) -> tuple[dict[str, list[dict]], set[str], set[str]]:
    """Resolve translatable items for a set of Object.Field names.

    Returns (object_fields, gvs_names, svs_names):
      - object_fields: object -> list of {name, label, inline:[(value, label)]} for custom
        fields (field labels + inline picklist values).
      - gvs_names: global value sets referenced (values translated once per set).
      - svs_names: standard value sets referenced.
    Routing matches expand_picklists.py. Warnings for unresolved fields go to stderr.
    """
    object_fields: dict[str, list[dict]] = {}
    gvs_names: set[str] = set()
    svs_names: set[str] = set()

    for object_field in sorted(fields):
        obj, field = object_field.split(".", 1)
        is_custom = field.endswith("__c")
        info = read_field(default_dir, obj, field)

        if info is None:
            # Uncustomized standard field: only standard picklists are translatable here.
            svs = STANDARD_VALUE_SETS.get(object_field)
            if svs:
                svs_names.add(svs)
            else:
                sys.stderr.write(f"warning: no field metadata for {object_field} (skipped)\n")
            continue

        entry = {"name": field, "label": info["label"], "inline": []} if is_custom else None

        if info["type"] in PICKLIST_TYPES:
            if not info["has_value_set"]:  # customized standard picklist -> StandardValueSet
                svs = STANDARD_VALUE_SETS.get(object_field)
                if svs:
                    svs_names.add(svs)
                else:
                    sys.stderr.write(
                        f"warning: {object_field} is a standard picklist with no mapping (values skipped)\n"
                    )
            elif info["value_set_name"]:  # global value set
                gvs_names.add(info["value_set_name"])
            elif entry is not None:  # inline value set -> object translation
                entry["inline"] = info["inline"]

        if entry is not None:
            object_fields.setdefault(obj, []).append(entry)

    return object_fields, gvs_names, svs_names


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Metadata API translation files (object / global value set / "
        "standard value set) for a target language from an ac_fields.csv-style CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input_file",
        metavar="FILE",
        help="CSV in ac_fields.csv format (Name = Object.Field; other columns ignored).",
    )
    parser.add_argument(
        "language",
        metavar="LANG",
        help="Target language code, e.g. de, fr, es, pt_BR.",
    )
    parser.add_argument(
        "-d",
        "--projectdir",
        type=Path,
        default=None,
        help="Project root used to locate metadata (default: current directory).",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        sys.exit(f"error: input file not found: {input_path}")
    lang = args.language
    default_dir = find_default_dir(args.projectdir)
    out_dir = Path.cwd()

    fields = read_fields_csv(input_path)
    object_fields, gvs_names, svs_names = collect(fields, default_dir)

    written: list[Path] = []
    for obj, flds in sorted(object_fields.items()):
        written.extend(write_object_translation(obj, flds, lang, out_dir))
    for name in sorted(gvs_names):
        labels = read_value_set_labels(
            default_dir / "globalValueSets" / f"{name}.globalValueSet-meta.xml", "customValue"
        )
        written.append(write_value_set_translation(name, labels, lang, out_dir, "global"))
    for name in sorted(svs_names):
        labels = read_value_set_labels(
            default_dir / "standardValueSets" / f"{name}.standardValueSet-meta.xml", "standardValue"
        )
        written.append(write_value_set_translation(name, labels, lang, out_dir, "standard"))

    print(
        f"Wrote {len(written)} source-format file(s) for '{lang}' under {out_dir}: "
        f"{len(object_fields)} object translation(s), {len(gvs_names)} global value set, "
        f"{len(svs_names)} standard value set.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
