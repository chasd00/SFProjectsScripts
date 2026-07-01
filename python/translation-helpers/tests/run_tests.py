#!/usr/bin/env python3
"""Tiny offline regression tests for the scripts/python tooling.

Runs the offline scripts (expand_picklists, translation_roundtrip to-csv/to-files) against a
small self-contained metadata fixture under tests/fixtures/ and asserts the output. No org
access required. Run with:

    python3 scripts/python/translation-helpers/tests/run_tests.py

Exits non-zero on the first failure.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
FIXTURES = HERE / "fixtures"
FIELD_CSV = FIXTURES / "ac_fields.csv"
PY = sys.executable
NO_CACHE = HERE / "_no_such_cache.json"  # force scripts to ignore optional JSON caches

passed = 0


def run(*args: str) -> None:
    result = subprocess.run([PY, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\n{result.stderr}")


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if not condition:
        raise AssertionError(f"FAIL: {name}{(' — ' + detail) if detail else ''}")
    passed += 1
    print(f"  ok: {name}")


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_expand_picklists(tmp: Path) -> None:
    print("expand_picklists:")
    out = tmp / "expanded.csv"
    run(str(SCRIPTS / "expand_picklists.py"), str(FIELD_CSV),
        "-d", str(FIXTURES), "-o", str(out), "--standard-picklists", str(NO_CACHE))
    rows = read_csv(out)
    # expand_picklists emits the picklist API value (fullName), not the label.
    pv = lambda name: sorted(r["PicklistValue"] for r in rows
                             if r["Name"] == name and r["PicklistValue"])
    check("inline picklist expanded", pv("Widget__c.Color__c") == ["BLUE", "RED"],
          str(pv("Widget__c.Color__c")))
    check("global value set expanded", pv("Widget__c.Size__c") == ["LARGE", "SMALL"],
          str(pv("Widget__c.Size__c")))
    notes = [r for r in rows if r["Name"] == "Widget__c.Notes__c"]
    check("non-picklist passes through", len(notes) == 1 and notes[0]["PicklistValue"] == "")


def test_translation_roundtrip(tmp: Path) -> None:
    print("translation_roundtrip:")
    csv_path = tmp / "translations_de.csv"
    run(str(SCRIPTS / "translation_roundtrip.py"), "to-csv", str(FIELD_CSV), "de",
        "-d", str(FIXTURES), "-o", str(csv_path), "--standard-labels", str(NO_CACHE))
    rows = read_csv(csv_path)
    by_type = lambda t: [r for r in rows if r["type"] == t]
    check("field-label rows", {r["field"] for r in by_type("field-label")} ==
          {"Color__c", "Size__c", "Notes__c"})
    check("inline picklist rows", sorted(r["masterLabel"] for r in by_type("field-picklist"))
          == ["Blue", "Red"])
    check("global-value rows", sorted(r["masterLabel"] for r in by_type("global-value"))
          == ["Large", "Small"])

    # Fill translations ("de::" prefix) and round-trip back to files.
    for r in rows:
        r["translation"] = "de::" + r["source"] if r["source"] else ""
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    out = tmp / "out"
    run(str(SCRIPTS / "translation_roundtrip.py"), "to-files", str(csv_path), "--dir", str(out))
    field_file = out / "objectTranslations" / "Widget__c-de" / "Color__c.fieldTranslation-meta.xml"
    gvs_file = out / "globalValueSetTranslations" / "SizeSet-de.globalValueSetTranslation-meta.xml"
    check("field translation file written", field_file.is_file())
    text = field_file.read_text() if field_file.is_file() else ""
    check("field label translated", "<label>de::Color</label>" in text)
    check("picklist value translated", "<translation>de::Red</translation>" in text)
    check("global value set file written", gvs_file.is_file())
    check("global value translated",
          "de::Small" in (gvs_file.read_text() if gvs_file.is_file() else ""))


def test_prefill(tmp: Path) -> None:
    """--retrieve-existing's pre-fill step: merge existing <lang> files into rows (offline)."""
    print("translation_roundtrip prefill:")
    sys.path.insert(0, str(SCRIPTS))
    from translation_roundtrip import prefill_rows  # imported here to keep top offline-only

    default_dir = tmp / "default"
    field_dir = default_dir / "objectTranslations" / "Widget__c-de"
    field_dir.mkdir(parents=True)
    (field_dir / "Color__c.fieldTranslation-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomFieldTranslation xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        "    <label>de::Color</label>\n    <name>Color__c</name>\n"
        "    <picklistValues>\n        <masterLabel>Red</masterLabel>\n"
        "        <translation>de::Red</translation>\n    </picklistValues>\n"
        "</CustomFieldTranslation>\n"
    )
    gvs_dir = default_dir / "globalValueSetTranslations"
    gvs_dir.mkdir(parents=True)
    (gvs_dir / "SizeSet-de.globalValueSetTranslation-meta.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<GlobalValueSetTranslation xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        "    <valueTranslation>\n        <masterLabel>Small</masterLabel>\n"
        "        <translation>de::Small</translation>\n    </valueTranslation>\n"
        "</GlobalValueSetTranslation>\n"
    )

    field_file = "objectTranslations/Widget__c-de/Color__c.fieldTranslation-meta.xml"
    gvs_file = "globalValueSetTranslations/SizeSet-de.globalValueSetTranslation-meta.xml"
    rows = [
        {"file": field_file, "type": "field-label", "field": "Color__c", "masterLabel": "", "translation": ""},
        {"file": field_file, "type": "field-picklist", "field": "Color__c", "masterLabel": "Red", "translation": ""},
        {"file": field_file, "type": "field-picklist", "field": "Color__c", "masterLabel": "Blue", "translation": ""},
        {"file": gvs_file, "type": "global-value", "field": "", "masterLabel": "Small", "translation": ""},
    ]
    filled = prefill_rows(rows, default_dir)
    check("prefill count", filled == 3, str(filled))
    check("label prefilled", rows[0]["translation"] == "de::Color")
    check("picklist prefilled", rows[1]["translation"] == "de::Red")
    check("missing stays blank", rows[2]["translation"] == "")
    check("global value prefilled", rows[3]["translation"] == "de::Small")


def test_new_types(tmp: Path) -> None:
    """Record types, validation rules, layout sections, and custom labels (to-csv + to-files)."""
    print("translation_roundtrip new types:")
    labels_list = FIXTURES / "labels.txt"
    csv_path = tmp / "nt_de.csv"
    run(str(SCRIPTS / "translation_roundtrip.py"), "to-csv", str(FIELD_CSV), "de",
        "-d", str(FIXTURES), "-o", str(csv_path), "--standard-labels", str(NO_CACHE),
        "--labels-list", str(labels_list))
    rows = read_csv(csv_path)
    src = lambda t: {(r["field"], r["masterLabel"]): r["source"] for r in rows if r["type"] == t}
    check("record-type row", src("record-type").get(("Premium", "")) == "Premium Widget")
    check("validation-rule row", src("validation-rule").get(("Require_Color", "")) == "Color is required.")
    check("layout-section row (labelled only)",
          src("layout-section") == {("Widget Layout", "Widget Details"): "Widget Details"})
    check("custom-label row (filtered to list)",
          src("custom-label") == {("Greeting", ""): "Hello"})  # Farewell excluded

    # Fill and round-trip to files.
    for r in rows:
        r["translation"] = "de::" + r["source"] if r["source"] else ""
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    out = tmp / "nt_out"
    run(str(SCRIPTS / "translation_roundtrip.py"), "to-files", str(csv_path), "--dir", str(out))

    container = (out / "objectTranslations" / "Widget__c-de" /
                 "Widget__c-de.objectTranslation-meta.xml").read_text()
    check("recordTypes inline in container", "<name>Premium</name>" in container
          and "<label>de::Premium Widget</label>" in container)
    check("validationRules inline in container",
          "<errorMessage>de::Color is required.</errorMessage>" in container)
    check("layouts inline in container", "<layout>Widget Layout</layout>" in container
          and "<section>Widget Details</section>" in container)
    # Container child order must be caseValues -> layouts -> recordTypes -> validationRules.
    check("container child order",
          container.find("<layouts>") < container.find("<recordTypes>") < container.find("<validationRules>"))
    labels_file = (out / "translations" / "de.translation-meta.xml").read_text()
    check("custom label in Translations file",
          "<name>Greeting</name>" in labels_file and "<label>de::Hello</label>" in labels_file)

    # Pre-fill round-trip: the values just written are read back by the existing-file reader.
    sys.path.insert(0, str(SCRIPTS))
    from translation_roundtrip import read_existing_file  # noqa: E402
    rt = read_existing_file(out / "objectTranslations" / "Widget__c-de" /
                            "Widget__c-de.objectTranslation-meta.xml")
    check("prefill reads record-type", rt.get(("record-type", "Premium", "")) == "de::Premium Widget")
    check("prefill reads layout-section",
          rt.get(("layout-section", "Widget Layout", "Widget Details")) == "de::Widget Details")
    cl = read_existing_file(out / "translations" / "de.translation-meta.xml")
    check("prefill reads custom-label", cl.get(("custom-label", "Greeting", "")) == "de::Hello")


def test_gen_layout_list(tmp: Path) -> None:
    """Offline parts of gen_layout_list: object scoping, filtering, manifest build."""
    print("gen_layout_list:")
    sys.path.insert(0, str(SCRIPTS))
    from gen_layout_list import build_manifest, filter_layouts, referenced_objects

    check("referenced objects from CSV", referenced_objects(FIELD_CSV) == {"Widget__c"})
    all_layouts = ["Widget__c-Widget Layout", "Account-Account Layout",
                   "Widget__c-Minimal", "NoDashLayout"]
    kept = filter_layouts(all_layouts, {"Widget__c"})
    check("filter keeps only referenced objects' layouts",
          kept == ["Widget__c-Minimal", "Widget__c-Widget Layout"], str(kept))
    manifest = build_manifest(kept, "62.0")
    check("manifest well-formed + scoped",
          "<name>Layout</name>" in manifest
          and "<members>Widget__c-Widget Layout</members>" in manifest
          and "<version>62.0</version>" in manifest)
    import xml.etree.ElementTree as ET
    ET.fromstring(manifest)  # raises if malformed
    check("manifest parses as XML", True)


def test_gen_manifest(tmp: Path) -> None:
    """Offline parts of gen_manifest: object scoping, en_US filtering, manifest build."""
    print("gen_manifest:")
    sys.path.insert(0, str(SCRIPTS))
    from gen_manifest import (build_manifest, en_us_translation_members,
                              referenced_objects, standard_objects)
    import xml.etree.ElementTree as ET

    # A CSV mixing custom + standard objects incl. Task (-> Activity).
    csv_path = tmp / "mixed.csv"
    csv_path.write_text("Name\nWidget__c.Color__c\nAccount.Industry\nTask.Subject\n")
    objs = referenced_objects(csv_path)
    check("referenced objects", objs == {"Widget__c", "Account", "Task"})
    check("standard objects + Activity",
          standard_objects(objs) == ["Account", "Activity", "Task"])

    full = ["Account-en_US", "Account-de", "Task-en_US", "Widget__c-en_US", "Contact-en_US"]
    members = en_us_translation_members(full, ["Account", "Activity", "Task"])
    check("en_US filtered to referenced standard objects",
          members == ["Account-en_US", "Task-en_US"], str(members))

    manifest = build_manifest([
        ("CustomObject", ["*", "Account", "Activity", "Task"]),
        ("CustomObjectTranslation", members),
        ("StandardValueSet", []),  # empty -> skipped
        ("GlobalValueSet", ["*"]),
    ], "62.0")
    check("manifest skips empty types", "StandardValueSet" not in manifest)
    check("manifest has wildcard + explicit members",
          "<members>*</members>" in manifest and "<members>Account</members>" in manifest
          and "<version>62.0</version>" in manifest)
    ET.fromstring(manifest)  # raises if malformed
    check("manifest parses as XML", True)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            test_expand_picklists(tmp)
            test_translation_roundtrip(tmp)
            test_prefill(tmp)
            test_new_types(tmp)
            test_gen_layout_list(tmp)
            test_gen_manifest(tmp)
        except AssertionError as e:
            print(f"\n{e}", file=sys.stderr)
            sys.exit(1)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()
