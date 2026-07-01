#!/usr/bin/env python3
"""Translate Salesforce fields via a CSV round-trip.

End-to-end flow, two modes:

  to-csv   Given a list of fields (ac_fields.csv format) and a target language, produce a
           CSV with every value that needs translating -- field labels, inline picklist
           values, global value set values, and standard value set values -- with a blank
           `translation` column for a translator/TMS to fill.

  to-files Read that filled CSV and produce deployable SFDX source-format translation files
           (objectTranslations/, globalValueSetTranslations/, standardValueSetTranslations/,
           translations/).

Beyond fields, to-csv also covers, scoped to the referenced objects: record-type labels,
validation-rule error messages, and layout section headings (all inline in each object's
CustomObjectTranslation container). Custom labels are chosen via a separate --labels-list
(API names) and translated through the global Translations type (translations/<lang>...).

CSV columns: file, type, component, field, masterLabel, source, translation. The `file`
column is the source-format path each row belongs to; `type` is one of field-label,
field-picklist, global-value, standard-value, standard-field (a RENAMED standard field,
keyed by its rename token), object-label, record-type, validation-rule, layout-section, or
custom-label. Only edit the `translation` column.

Renamed standard field/object labels are read from the org default-language (en_US)
CustomObjectTranslation (retrieved via manifest/package.xml); unchanged standard labels are
excluded by default since Salesforce auto-translates them (override with
--include-unchanged-standard-fields).

By default to-csv emits a blank `translation` column. Pass --retrieve-existing (with
--target-org) to first retrieve the existing <lang> translation metadata for the referenced
components (retrieve only -- never deploys) and pre-fill `translation` from any values that
already exist; rows with no existing translation stay blank.

Reads/writes local files only (never deploys). For to-csv, metadata is located via
-d/--projectdir. For to-files, output is written under --dir (default: current directory).

Multiple languages can be produced in one shot: pass a comma-separated LANG to to-csv
(outputs <stem>_<lang>.csv per language), and pass several CSVs to to-files.

Examples:
  python3 scripts/python/translation_roundtrip.py to-csv ac_fields.csv de -o translations.csv
  python3 scripts/python/translation_roundtrip.py to-csv ac_fields.csv de,fr,es -o translations.csv
  python3 scripts/python/translation_roundtrip.py to-csv ac_fields.csv de --retrieve-existing --target-org myorg
  python3 scripts/python/translation_roundtrip.py to-csv ac_fields.csv de --labels-list labels.txt -o translations.csv
  # ...fill in the translation column of each CSV...
  python3 scripts/python/translation_roundtrip.py to-files translations_de.csv translations_fr.csv --dir out
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

# Reuse the field resolver so to-csv routes values exactly like the file generator.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from expand_picklists import find_default_dir  # noqa: E402
from to_translation import collect, read_fields_csv, read_value_set_labels  # noqa: E402

NS = "http://soap.sforce.com/2006/04/metadata"
COLUMNS = ["file", "type", "component", "field", "masterLabel", "source", "translation"]
FIELD_SUFFIX = ".fieldTranslation-meta.xml"
GVS_SUFFIX = ".globalValueSetTranslation-meta.xml"
SVS_SUFFIX = ".standardValueSetTranslation-meta.xml"
CONTAINER_SUFFIX = ".objectTranslation-meta.xml"
# Custom labels translate via the global Translations type, not CustomObjectTranslation.
TRANSLATIONS_SUFFIX = ".translation-meta.xml"  # note: NOT objectTranslation (capital T)
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]


# --------------------------------------------------------------------------- to-csv


def read_renames(default_dir: Path, obj: str) -> tuple[list[dict], list[tuple[bool, str]]]:
    """Read RENAMED standard fields/object label from the org default-language (en_US)
    CustomObjectTranslation for `obj`.

    Standard field/object label overrides are NOT in CustomField metadata; they live here,
    keyed by an internal rename token (e.g. `account_currency`) with the label in
    <caseValues>, not <label>. Unchanged standard labels are absent (Salesforce auto-
    translates them). Returns (field_renames, object_labels):
      - field_renames: [{token, source}] (the en_US singular label per renamed field)
      - object_labels: [(plural, source)] from the container's object-name caseValues
    """
    folder = default_dir / "objectTranslations" / f"{obj}-en_US"
    field_renames: list[dict] = []
    object_labels: list[tuple[bool, str]] = []
    if not folder.is_dir():
        return field_renames, object_labels

    for ff in sorted(folder.glob(f"*{FIELD_SUFFIX}")):
        root = ET.parse(ff).getroot()
        cases = root.findall("{*}caseValues")
        if not cases:  # a custom-field translation (uses <label>), not a standard rename
            continue
        token = root.findtext("{*}name") or ff.name[: -len(FIELD_SUFFIX)]
        source = ""
        for c in cases:  # prefer the singular (plural=false) value
            value = c.findtext("{*}value") or ""
            if (c.findtext("{*}plural") or "false") == "false":
                source = value
                break
            source = source or value
        field_renames.append({"token": token, "source": source})

    container = folder / f"{obj}-en_US.objectTranslation-meta.xml"
    if container.is_file():
        root = ET.parse(container).getroot()
        for c in root.findall("{*}caseValues"):
            plural = (c.findtext("{*}plural") or "false") == "true"
            object_labels.append((plural, c.findtext("{*}value") or ""))

    return field_renames, object_labels


def read_record_types(default_dir: Path, obj: str) -> list[tuple[str, str]]:
    """Return [(name, label)] for an object's record types (source text to translate)."""
    folder = default_dir / "objects" / obj / "recordTypes"
    out: list[tuple[str, str]] = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.recordType-meta.xml")):
        root = ET.parse(f).getroot()
        name = root.findtext("{*}fullName") or f.name[: -len(".recordType-meta.xml")]
        label = root.findtext("{*}label") or ""
        if label:
            out.append((name, label))
    return out


def read_validation_rules(default_dir: Path, obj: str) -> list[tuple[str, str]]:
    """Return [(name, errorMessage)] for an object's validation rules."""
    folder = default_dir / "objects" / obj / "validationRules"
    out: list[tuple[str, str]] = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.validationRule-meta.xml")):
        root = ET.parse(f).getroot()
        name = root.findtext("{*}fullName") or f.name[: -len(".validationRule-meta.xml")]
        msg = root.findtext("{*}errorMessage") or ""
        if msg:
            out.append((name, msg))
    return out


def read_layout_sections(default_dir: Path, obj: str) -> list[tuple[str, str]]:
    """Return [(layout, section_label)] for an object's layout section headings.

    Layout fullNames are `<Obj>-<Layout Name>`. Only sections with a non-empty <label>
    are translatable; system/standard sections without a label are skipped.
    """
    folder = default_dir / "layouts"
    out: list[tuple[str, str]] = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob(f"{obj}-*.layout-meta.xml")):
        layout = f.name[: -len(".layout-meta.xml")].split("-", 1)[1]
        root = ET.parse(f).getroot()
        for sec in root.findall("{*}layoutSections"):
            label = sec.findtext("{*}label") or ""
            if label:
                out.append((layout, label))
    return out


def read_labels_list(path: Path) -> list[str]:
    """Read custom-label API names from a labels list (one per line, or a CSV's first/Name col).

    Blank lines and lines starting with '#' are ignored; a leading 'Name' header is skipped.
    """
    names: list[str] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = line.split(",", 1)[0].strip()
            if name and name != "Name":
                names.append(name)
    return names


def read_custom_labels(default_dir: Path, names: list[str]) -> list[tuple[str, str]]:
    """Return [(name, value)] for the requested custom labels, in the input order.

    Reads labels/CustomLabels.labels-meta.xml; unknown names are warned and skipped.
    """
    file = default_dir / "labels" / "CustomLabels.labels-meta.xml"
    out: list[tuple[str, str]] = []
    if not names:
        return out
    values: dict[str, str] = {}
    if file.is_file():
        root = ET.parse(file).getroot()
        for label in root.findall("{*}labels"):
            full = label.findtext("{*}fullName") or ""
            if full:
                values[full] = label.findtext("{*}value") or ""
    for name in names:
        if name in values:
            out.append((name, values[name]))
        else:
            sys.stderr.write(f"warning: custom label '{name}' not found in metadata (skipped)\n")
    return out


def build_rows(
    object_fields: dict[str, list[dict]],
    gvs_names: set[str],
    svs_names: set[str],
    lang: str,
    default_dir: Path,
    referenced_fields: set[str],
    standard_labels: dict,
    include_unchanged: bool,
    filter_to_input: bool = False,
    custom_labels: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Turn resolved translatable items into CSV rows with their target file paths."""
    rows: list[dict] = []

    for obj, fields in sorted(object_fields.items()):
        base = f"objectTranslations/{obj}-{lang}"
        for f in sorted(fields, key=lambda x: x["name"]):
            file = f"{base}/{f['name']}{FIELD_SUFFIX}"
            if f["label"]:
                rows.append({
                    "file": file, "type": "field-label", "component": obj,
                    "field": f["name"], "masterLabel": "", "source": f["label"],
                    "translation": "",
                })
            for value, label in f["inline"]:
                master = label or value or ""
                rows.append({
                    "file": file, "type": "field-picklist", "component": obj,
                    "field": f["name"], "masterLabel": master, "source": master,
                    "translation": "",
                })

    # Standard object/field labels: only RENAMED ones, sourced from the org default-language
    # (en_US) CustomObjectTranslation. Unchanged standard labels are auto-translated by
    # Salesforce, so they are excluded by default.
    referenced_objects = sorted({
        f.split(".", 1)[0] for f in referenced_fields
        if "." in f and "__" not in f.split(".", 1)[0]
    })
    # For --filter-standard-fields-to-input: reverse the describe label map (Object.Field ->
    # label) into label -> {Object.Field}, so a rename token's en_US label resolves to API
    # field name(s) and can be checked against the input field list.
    label_to_fields: dict[str, set[str]] = {}
    if filter_to_input:
        for object_field, label in standard_labels.get("fields", {}).items():
            label_to_fields.setdefault(label, set()).add(object_field)

    for obj in referenced_objects:
        field_renames, object_labels = read_renames(default_dir, obj)
        base = f"objectTranslations/{obj}-{lang}"
        container = f"{base}/{obj}-{lang}.objectTranslation-meta.xml"
        for plural, source in object_labels:
            rows.append({
                "file": container, "type": "object-label", "component": obj,
                "field": "plural" if plural else "singular", "masterLabel": "",
                "source": source, "translation": "",
            })
        for r in field_renames:
            if filter_to_input:
                resolved = label_to_fields.get(r["source"])
                if resolved is None:
                    sys.stderr.write(
                        f"warning: could not map rename '{r['token']}' on {obj} to an "
                        "Object.Field (kept; verify manually)\n"
                    )
                elif not (resolved & referenced_fields):
                    continue  # resolved to a field not in the input list -> drop
            rows.append({
                "file": f"{base}/{r['token']}{FIELD_SUFFIX}", "type": "standard-field",
                "component": obj, "field": r["token"], "masterLabel": "",
                "source": r["source"], "translation": "",
            })

    # Opt-in: also translate UNCHANGED standard field labels (describe-based). Usually
    # unnecessary (Salesforce auto-translates them) and not deploy-correct for standard
    # fields, so off by default.
    if include_unchanged:
        field_labels = standard_labels.get("fields", {})
        for object_field in sorted(f for f in referenced_fields if f in field_labels):
            obj, field = object_field.split(".", 1)
            rows.append({
                "file": f"objectTranslations/{obj}-{lang}/{field}{FIELD_SUFFIX}",
                "type": "field-label", "component": obj, "field": field,
                "masterLabel": "", "source": field_labels[object_field], "translation": "",
            })

    # Record types, validation-rule error messages, and layout section headings live inline
    # in each object's CustomObjectTranslation container, scoped to referenced objects.
    referenced_objects_all = sorted({
        f.split(".", 1)[0] for f in referenced_fields if "." in f
    })
    for obj in referenced_objects_all:
        container = f"objectTranslations/{obj}-{lang}/{obj}-{lang}{CONTAINER_SUFFIX}"
        for rt_name, label in read_record_types(default_dir, obj):
            rows.append({
                "file": container, "type": "record-type", "component": obj,
                "field": rt_name, "masterLabel": "", "source": label, "translation": "",
            })
        for vr_name, msg in read_validation_rules(default_dir, obj):
            rows.append({
                "file": container, "type": "validation-rule", "component": obj,
                "field": vr_name, "masterLabel": "", "source": msg, "translation": "",
            })
        for layout, section in read_layout_sections(default_dir, obj):
            rows.append({
                "file": container, "type": "layout-section", "component": obj,
                "field": layout, "masterLabel": section, "source": section, "translation": "",
            })

    # Custom labels: global Translations type, chosen via a separate --labels-list input.
    if custom_labels:
        translations_file = f"translations/{lang}{TRANSLATIONS_SUFFIX}"
        for name, value in custom_labels:
            rows.append({
                "file": translations_file, "type": "custom-label", "component": "(global)",
                "field": name, "masterLabel": "", "source": value, "translation": "",
            })

    for name in sorted(gvs_names):
        labels = read_value_set_labels(
            default_dir / "globalValueSets" / f"{name}.globalValueSet-meta.xml", "customValue"
        )
        file = f"globalValueSetTranslations/{name}-{lang}{GVS_SUFFIX}"
        for label in labels:
            rows.append({
                "file": file, "type": "global-value", "component": name, "field": "",
                "masterLabel": label, "source": label, "translation": "",
            })

    for name in sorted(svs_names):
        labels = read_value_set_labels(
            default_dir / "standardValueSets" / f"{name}.standardValueSet-meta.xml", "standardValue"
        )
        file = f"standardValueSetTranslations/{name}-{lang}{SVS_SUFFIX}"
        for label in labels:
            rows.append({
                "file": file, "type": "standard-value", "component": name, "field": "",
                "masterLabel": label, "source": label, "translation": "",
            })

    return rows


def retrieve_existing(
    objects: set[str],
    gvs_names: set[str],
    svs_names: set[str],
    lang: str,
    target_org: str,
    default_dir: Path,
    include_translations: bool = False,
) -> None:
    """Retrieve the existing <lang> translation metadata for the referenced components.

    Retrieve only (never deploys). Builds a precise component list from what collect()
    resolved and shells out to `sf project retrieve start`, writing the files into the
    project source tree so the pre-fill step can read them. Missing members (e.g. a
    language not yet translated for an object) are not errors; sf simply skips them.

    Record types, validation rules, and layout sections ride along inside
    CustomObjectTranslation. Custom labels are separate: when include_translations is set
    (custom labels requested), the global Translations:<lang> is also retrieved.
    """
    components = (
        [f"CustomObjectTranslation:{obj}-{lang}" for obj in sorted(objects)]
        + [f"GlobalValueSetTranslation:{name}-{lang}" for name in sorted(gvs_names)]
        + [f"StandardValueSetTranslation:{name}-{lang}" for name in sorted(svs_names)]
    )
    if include_translations:
        components.append(f"Translations:{lang}")
    if not components:
        return

    cmd = _SF + ["project", "retrieve", "start", "-o", target_org]
    for c in components:
        cmd += ["-m", c]
    # Project root holds sfdx-project.json; default_dir is <root>/force-app/main/default.
    cwd = default_dir.parents[2] if len(default_dir.parents) >= 3 else None
    print(f"Retrieving {len(components)} existing '{lang}' translation component(s) "
          f"from {target_org}...", file=sys.stderr)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        sys.stderr.write(
            f"warning: retrieve for '{lang}' failed (continuing without pre-fill):\n"
            f"{result.stderr.strip() or result.stdout.strip()}\n"
        )


def read_existing_file(path: Path) -> dict[tuple[str, str, str], str]:
    """Map (type, field, masterLabel) -> existing translation for one translation file.

    Keys mirror how build_rows identifies each row, so a row's existing value is a direct
    dict lookup. Empty translations are omitted so they don't overwrite anything.
    """
    out: dict[tuple[str, str, str], str] = {}
    if not path.is_file():
        return out
    root = ET.parse(path).getroot()
    name = path.name

    if name.endswith(FIELD_SUFFIX):
        field = name[: -len(FIELD_SUFFIX)]
        cases = root.findall("{*}caseValues")
        if cases:  # renamed standard field (caseValues + token, no <label>)
            value = ""
            for c in cases:  # prefer the singular (plural=false) value
                v = c.findtext("{*}value") or ""
                if (c.findtext("{*}plural") or "false") == "false":
                    value = v
                    break
                value = value or v
            if value:
                out[("standard-field", field, "")] = value
        else:
            label = root.findtext("{*}label")
            if label:
                out[("field-label", field, "")] = label
            for pv in root.findall("{*}picklistValues"):
                ml = pv.findtext("{*}masterLabel") or ""
                tr = pv.findtext("{*}translation") or ""
                if tr:
                    out[("field-picklist", field, ml)] = tr
    elif name.endswith(CONTAINER_SUFFIX):
        for c in root.findall("{*}caseValues"):
            plural = (c.findtext("{*}plural") or "false") == "true"
            v = c.findtext("{*}value") or ""
            if v:
                out[("object-label", "plural" if plural else "singular", "")] = v
        for rt in root.findall("{*}recordTypes"):
            n, lbl = rt.findtext("{*}name") or "", rt.findtext("{*}label") or ""
            if n and lbl:
                out[("record-type", n, "")] = lbl
        for vr in root.findall("{*}validationRules"):
            n, msg = vr.findtext("{*}name") or "", vr.findtext("{*}errorMessage") or ""
            if n and msg:
                out[("validation-rule", n, "")] = msg
        for lay in root.findall("{*}layouts"):
            layout = lay.findtext("{*}layout") or ""
            for sec in lay.findall("{*}sections"):
                section = sec.findtext("{*}section") or ""
                lbl = sec.findtext("{*}label") or ""
                if layout and section and lbl:
                    out[("layout-section", layout, section)] = lbl
    elif name.endswith(GVS_SUFFIX) or name.endswith(SVS_SUFFIX):
        type_ = "global-value" if name.endswith(GVS_SUFFIX) else "standard-value"
        for vt in root.findall("{*}valueTranslation"):
            ml = vt.findtext("{*}masterLabel") or ""
            tr = vt.findtext("{*}translation") or ""
            if tr:
                out[(type_, "", ml)] = tr
    elif name.endswith(TRANSLATIONS_SUFFIX):  # global Translations (custom labels)
        for cl in root.findall("{*}customLabels"):
            n, lbl = cl.findtext("{*}name") or "", cl.findtext("{*}label") or ""
            if n and lbl:
                out[("custom-label", n, "")] = lbl
    return out


def prefill_rows(rows: list[dict], default_dir: Path) -> int:
    """Fill each row's `translation` from existing <lang> files under default_dir.

    Row `file` paths are source-format relative paths (e.g. objectTranslations/Account-de/
    Name.fieldTranslation-meta.xml), which is exactly where retrieve writes, so the existing
    file for a row is default_dir/<file>. Returns how many rows were filled.
    """
    cache: dict[Path, dict[tuple[str, str, str], str]] = {}
    filled = 0
    for r in rows:
        path = default_dir / r["file"]
        existing = cache.get(path)
        if existing is None:
            existing = cache[path] = read_existing_file(path)
        value = existing.get((r["type"], r["field"], r["masterLabel"]))
        if value:
            r["translation"] = value
            filled += 1
    return filled


def output_for_lang(output: Path, lang: str, multiple: bool) -> Path:
    """One output path per language: as-is for a single language, '<stem>_<lang>' for many."""
    if not multiple:
        return output
    return output.with_name(f"{output.stem}_{lang}{output.suffix or '.csv'}")


def to_csv(
    input_path: Path,
    languages: list[str],
    projectdir: Path | None,
    output: Path,
    labels_path: Path,
    include_unchanged: bool,
    filter_to_input: bool,
    retrieve: bool,
    target_org: str | None,
    labels_list_path: Path | None,
) -> None:
    default_dir = find_default_dir(projectdir)
    fields = read_fields_csv(input_path)
    # Language-independent resolution is done once and reused for every language.
    object_fields, gvs_names, svs_names = collect(fields, default_dir)
    # Every referenced object (custom + standard) can carry a CustomObjectTranslation.
    referenced_objects_all = {f.split(".", 1)[0] for f in fields if "." in f}

    # Custom labels come from a separate list (not tied to the field CSV); resolve once.
    custom_labels: list[tuple[str, str]] = []
    if labels_list_path is not None:
        custom_labels = read_custom_labels(default_dir, read_labels_list(labels_list_path))

    # Describe-based labels feed the unchanged-standard-fields path and the input filter.
    standard_labels: dict = {}
    if include_unchanged or filter_to_input:
        if labels_path.is_file():
            with open(labels_path, encoding="utf-8") as fh:
                standard_labels = json.load(fh)
        else:
            sys.stderr.write(
                f"note: {labels_path} not found; --include-unchanged-standard-fields / "
                "--filter-standard-fields-to-input need it (run gen_standard_labels.py)\n"
            )

    multiple = len(languages) > 1
    for lang in languages:
        if retrieve:
            retrieve_existing(referenced_objects_all, gvs_names, svs_names, lang,
                              target_org, default_dir, include_translations=bool(custom_labels))
        rows = build_rows(object_fields, gvs_names, svs_names, lang, default_dir,
                          fields, standard_labels, include_unchanged, filter_to_input,
                          custom_labels)
        filled = prefill_rows(rows, default_dir) if retrieve else 0
        out = output_for_lang(output, lang, multiple)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        counts = Counter(r["type"] for r in rows)
        print(
            f"Wrote {len(rows)} translatable slot(s) for '{lang}' to {out} — "
            f"{counts['field-label']} custom field label, {counts['field-picklist']} inline "
            f"picklist, {counts['global-value']} global value, {counts['standard-value']} "
            f"standard value, {counts['standard-field']} renamed standard field, "
            f"{counts['object-label']} object label, {counts['record-type']} record type, "
            f"{counts['validation-rule']} validation rule, {counts['layout-section']} layout "
            f"section, {counts['custom-label']} custom label."
            + (f" Pre-filled {filled} from existing translations." if retrieve else ""),
            file=sys.stderr,
        )
        if counts["standard-field"] and not filter_to_input:
            sys.stderr.write(
                f"NOTICE: the output includes ALL {counts['standard-field']} renamed standard "
                "field(s) on the referenced standard objects, so it may contain more renamed "
                "standard fields than your input field list. Pass "
                "--filter-standard-fields-to-input (with gen_standard_labels.py's "
                "standard_labels.json) to keep only those matching your input.\n"
            )


# --------------------------------------------------------------------------- to-files


def write_field_file(path: Path, field: str, label: str, picks: list[tuple[str, str]]) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<CustomFieldTranslation xmlns="{NS}">']
    lines.append(f"    <label>{escape(label)}</label>")
    lines.append(f"    <name>{escape(field)}</name>")
    for ml, tr in picks:
        lines.append("    <picklistValues>")
        lines.append(f"        <masterLabel>{escape(ml)}</masterLabel>")
        lines.append(f"        <translation>{escape(tr)}</translation>")
        lines.append("    </picklistValues>")
    lines.append("</CustomFieldTranslation>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_standard_field_file(path: Path, token: str, translation: str) -> None:
    """Write a renamed-standard-field translation (caseValues + rename token, no <label>)."""
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<CustomFieldTranslation xmlns="{NS}">\n'
        "    <caseValues>\n"
        "        <plural>false</plural>\n"
        f"        <value>{escape(translation)}</value>\n"
        "    </caseValues>\n"
        f"    <name>{escape(token)}</name>\n"
        "</CustomFieldTranslation>\n",
        encoding="utf-8",
    )


def write_value_set_file(path: Path, root_tag: str, entries: list[tuple[str, str]]) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<{root_tag} xmlns="{NS}">']
    for ml, tr in entries:
        lines.append("    <valueTranslation>")
        lines.append(f"        <masterLabel>{escape(ml)}</masterLabel>")
        lines.append(f"        <translation>{escape(tr)}</translation>")
        lines.append("    </valueTranslation>")
    lines.append(f"</{root_tag}>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_container(
    path: Path,
    cases: list[tuple[bool, str]] | None = None,
    layouts: list[tuple[str, str, str]] | None = None,
    record_types: list[tuple[str, str]] | None = None,
    validation_rules: list[tuple[str, str]] | None = None,
) -> None:
    """Write the object-translation container with all inline (non-field) translations.

    Elements are emitted in CustomObjectTranslation child order: caseValues, layouts,
    recordTypes, validationRules (fields stay in their own .fieldTranslation files).
      - layouts: list of (layout, section_master, section_translation)
      - record_types / validation_rules: list of (name, translation)
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<CustomObjectTranslation xmlns="{NS}">']
    for plural, value in cases or []:
        lines.append("    <caseValues>")
        lines.append(f"        <plural>{'true' if plural else 'false'}</plural>")
        lines.append(f"        <value>{escape(value)}</value>")
        lines.append("    </caseValues>")
    # Group sections by layout so each layout is emitted once.
    by_layout: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for layout, section, tr in layouts or []:
        by_layout[layout].append((section, tr))
    for layout in sorted(by_layout):
        lines.append("    <layouts>")
        lines.append(f"        <layout>{escape(layout)}</layout>")
        for section, tr in by_layout[layout]:
            lines.append("        <sections>")
            lines.append(f"            <label>{escape(tr)}</label>")
            lines.append(f"            <section>{escape(section)}</section>")
            lines.append("        </sections>")
        lines.append("    </layouts>")
    for name, tr in record_types or []:
        lines.append("    <recordTypes>")
        lines.append(f"        <label>{escape(tr)}</label>")
        lines.append(f"        <name>{escape(name)}</name>")
        lines.append("    </recordTypes>")
    for name, tr in validation_rules or []:
        lines.append("    <validationRules>")
        lines.append(f"        <errorMessage>{escape(tr)}</errorMessage>")
        lines.append(f"        <name>{escape(name)}</name>")
        lines.append("    </validationRules>")
    lines.append("</CustomObjectTranslation>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_translations_file(path: Path, labels: list[tuple[str, str]]) -> None:
    """Write the global Translations file holding custom-label translations."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Translations xmlns="{NS}">']
    for name, tr in labels:
        lines.append("    <customLabels>")
        lines.append(f"        <label>{escape(tr)}</label>")
        lines.append(f"        <name>{escape(name)}</name>")
        lines.append("    </customLabels>")
    lines.append("</Translations>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_files(root: Path, csv_path: Path) -> None:
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_file[r["file"]].append(r)

    files_written = 0
    object_folders: set[Path] = set()
    containers_written: set[Path] = set()
    for rel, frows in by_file.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(FIELD_SUFFIX):
            field = path.name[: -len(FIELD_SUFFIX)]
            if any(r["type"] == "standard-field" for r in frows):
                # Renamed standard field: caseValues + rename token (no <label>).
                tr = next((r["translation"] for r in frows if r["type"] == "standard-field"), "")
                write_standard_field_file(path, field, tr)
            else:
                label = next((r["translation"] for r in frows if r["type"] == "field-label"), "")
                picks = [(r["masterLabel"], r["translation"]) for r in frows
                         if r["type"] == "field-picklist"]
                write_field_file(path, field, label, picks)
            object_folders.add(path.parent)
            files_written += 1
        elif rel.endswith(CONTAINER_SUFFIX):  # container: object labels + inline types
            cases = [(r["field"] == "plural", r["translation"]) for r in frows
                     if r["type"] == "object-label"]
            layouts = [(r["field"], r["masterLabel"], r["translation"]) for r in frows
                       if r["type"] == "layout-section"]
            record_types = [(r["field"], r["translation"]) for r in frows
                            if r["type"] == "record-type"]
            validation_rules = [(r["field"], r["translation"]) for r in frows
                                if r["type"] == "validation-rule"]
            write_container(path, cases, layouts, record_types, validation_rules)
            containers_written.add(path)
            object_folders.add(path.parent)
            files_written += 1
        elif rel.endswith(GVS_SUFFIX):
            write_value_set_file(path, "GlobalValueSetTranslation",
                                 [(r["masterLabel"], r["translation"]) for r in frows])
            files_written += 1
        elif rel.endswith(SVS_SUFFIX):
            write_value_set_file(path, "StandardValueSetTranslation",
                                 [(r["masterLabel"], r["translation"]) for r in frows])
            files_written += 1
        elif rel.endswith(TRANSLATIONS_SUFFIX):  # global Translations (custom labels)
            write_translations_file(path, [(r["field"], r["translation"]) for r in frows
                                           if r["type"] == "custom-label"])
            files_written += 1
        else:
            sys.stderr.write(f"warning: unrecognized translation file, skipped: {rel}\n")

    # Ensure every object folder has a container (empty if it had no object-label rows).
    for folder in object_folders:
        container = folder / f"{folder.name}.objectTranslation-meta.xml"
        if container not in containers_written:
            write_container(container)
            files_written += 1

    translated = sum(1 for r in rows if r.get("translation"))
    print(
        f"Wrote {files_written} translation file(s) under {root} from {csv_path} "
        f"({translated} of {len(rows)} slot(s) translated).",
        file=sys.stderr,
    )


def to_files_many(root: Path, csv_paths: list[Path]) -> None:
    """Run to_files for each CSV (each CSV is one language; paths encode the language)."""
    for csv_path in csv_paths:
        if not csv_path.is_file():
            sys.exit(f"error: CSV not found: {csv_path}")
        to_files(root, csv_path)


# --------------------------------------------------------------------------- cli


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate Salesforce fields via a CSV round-trip (to-csv, then to-files).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    pc = sub.add_parser("to-csv", help="field list -> CSV of values to translate")
    pc.add_argument("input_file", metavar="FIELDS_CSV",
                    help="CSV in ac_fields.csv format (Name = Object.Field).")
    pc.add_argument("language", metavar="LANG",
                    help="Target language code(s); comma-separate for a batch, e.g. de,fr,pt_BR. "
                    "With multiple, output files are named <output stem>_<lang>.csv.")
    pc.add_argument("-d", "--projectdir", type=Path, default=None,
                    help="Project root used to locate metadata (default: current directory).")
    pc.add_argument("-o", "--output", type=Path, default=Path("translations.csv"),
                    help="Output CSV (default: translations.csv).")
    pc.add_argument("--standard-labels", type=Path, default=Path("standard_labels.json"),
                    help="describe-based labels from gen_standard_labels.py, only used with "
                    "--include-unchanged-standard-fields (default: standard_labels.json).")
    pc.add_argument("--include-unchanged-standard-fields", action="store_true",
                    help="Also translate standard field labels that have NOT been renamed. "
                    "Off by default: unchanged standard labels are auto-translated by "
                    "Salesforce, so only renamed standard fields (from the en_US "
                    "CustomObjectTranslation) are included.")
    pc.add_argument("--filter-standard-fields-to-input", action="store_true",
                    help="Keep only renamed standard fields whose label maps to an "
                    "Object.Field in your input list (needs gen_standard_labels.py's "
                    "standard_labels.json). Unmappable renames are kept with a warning.")
    pc.add_argument("--retrieve-existing", action="store_true",
                    help="Before writing the CSV, retrieve the existing <lang> translation "
                    "metadata for the referenced components (retrieve only, never deploys) "
                    "and pre-fill the `translation` column from it. Requires --target-org. "
                    "Off by default; without it the script stays offline and emits blanks.")
    pc.add_argument("--target-org", default=None,
                    help="Org alias to retrieve from; required with --retrieve-existing.")
    pc.add_argument("--labels-list", type=Path, default=None,
                    help="File of custom-label API names (one per line, or a CSV with a "
                    "Name/first column) to also translate via the global Translations type. "
                    "Custom labels aren't in the field CSV, so they're chosen separately.")

    pf = sub.add_parser("to-files", help="filled CSV(s) -> deployable translation files")
    pf.add_argument("csv", type=Path, nargs="+",
                    help="One or more filled CSVs from to-csv (e.g. per-language batch).")
    pf.add_argument("--dir", type=Path, default=Path("."),
                    help="Root to write translation files under (default: current directory).")

    args = parser.parse_args()
    if args.mode == "to-csv":
        input_path = Path(args.input_file)
        if not input_path.is_file():
            sys.exit(f"error: input file not found: {input_path}")
        languages = [s.strip() for s in args.language.split(",") if s.strip()]
        if not languages:
            sys.exit("error: no target language given")
        if args.retrieve_existing and not args.target_org:
            sys.exit("error: --retrieve-existing requires --target-org")
        if args.labels_list is not None and not args.labels_list.is_file():
            sys.exit(f"error: labels list not found: {args.labels_list}")
        to_csv(input_path, languages, args.projectdir, args.output,
               args.standard_labels, args.include_unchanged_standard_fields,
               args.filter_standard_fields_to_input,
               args.retrieve_existing, args.target_org, args.labels_list)
    else:
        to_files_many(args.dir, args.csv)


if __name__ == "__main__":
    main()
