# Plan: extend the translation pipeline to 4 more metadata types

## Context

The translation pipeline (`scripts/python/translation_roundtrip.py` + helpers) currently
covers custom field labels, inline picklist values, global/standard value-set values,
renamed standard field labels, and object labels. The user wants to extend it to **record
types**, **validation-rule error messages**, **custom labels** (global `Translations`
type), and **layout section headings**.

**Deliverable of this task:** a step-by-step implementation roadmap written to a new docs
folder — `scripts/python/docs/translation-roadmap.md`. (This task writes the *plan doc*, not
the implementation.)

Decisions captured from the user:
- **Custom labels** are chosen via a **separate input list** (a `labels.txt`/CSV of label
  API names), since they aren't tied to the `ac_fields.csv` field list.
- **Layout section headings** are scoped to **referenced objects only** (objects appearing
  in the field list), not all layouts.

## Key facts established (read-only investigation)

- Record types (48) and validation rules (88) are **already retrieved** with the objects;
  source text is in their own metadata: `recordTypes/*.recordType-meta.xml` `<label>` and
  `validationRules/*.validationRule-meta.xml` `<errorMessage>`.
- Layouts (0) and custom labels (none) are **not retrieved** — manifest must add `Layout`
  (scoped) and `CustomLabels`.
- All four translate via `CustomObjectTranslation` **except custom labels**, which use the
  global `translations/<lang>.translation-meta.xml` (`<Translations><customLabels>`).
- In SFDX source format, only **fields** decompose into separate `.fieldTranslation-meta.xml`
  files; record types / validation rules / layouts stay **inline in the container**
  `<Obj>-<lang>.objectTranslation-meta.xml`. (Verify during implementation.)

## What the doc should contain (the roadmap to write)

### 0. Pre-req — metadata retrieval
- Add to `manifest/package.xml`: `CustomLabels` (member `CustomLabels`). Record types &
  validation rules already arrive with `CustomObject`.
- **Layouts (scoped):** static manifest wildcards can't scope by object, and layout
  fullNames are `Object-Layout Name`. Add a helper retrieve step: enumerate via
  `sf org list metadata -m Layout`, filter to referenced objects (prefix `<Obj>-`), and
  retrieve those members. Document the command; optionally a tiny `gen_layout_list.py` later.

### 1. CSV schema (reuse existing columns)
Columns stay `file, type, component, field, masterLabel, source, translation`. New `type`
values and their column mapping:
- `record-type` — file=container, component=Obj, field=record-type API name, source=`<label>`
- `validation-rule` — file=container, component=Obj, field=rule name, source=`<errorMessage>`
- `layout-section` — file=container, component=Obj, field=layout name, masterLabel=section
  name, source=section label (needs both layout + section to be unique)
- `custom-label` — file=`translations/<lang>.translation-meta.xml`, component=`(global)`,
  field=label API name, source=label `<value>`

### 2. `to-csv` (build readers + emit rows) — `translation_roundtrip.py`
Add offline readers (mirror existing `read_renames`/`read_value_set_labels` style):
- `read_record_types(default_dir, obj)` → `[(name, label)]` from `objects/<Obj>/recordTypes/`
- `read_validation_rules(default_dir, obj)` → `[(name, errorMessage)]` from `.../validationRules/`
- `read_layout_sections(default_dir, obj)` → `[(layout, section, label)]` from
  `layouts/<Obj>-*.layout-meta.xml` `<layoutSections><label>` (skip empty/standard sections)
- `read_custom_labels(default_dir, names)` → `[(name, value)]` from
  `labels/CustomLabels.labels-meta.xml`, filtered to `names`
In `build_rows`: for each **referenced object** emit record-type / validation-rule /
layout-section rows into that object's container path; emit custom-label rows (from the
labels list) into the global translations file. Add a `--labels-list <file>` arg to `to-csv`.

### 3. `to-files` (writers + routing) — `translation_roundtrip.py`
- **Container writer**: extend `write_container` to also emit `<recordTypes>`,
  `<validationRules>`, `<layouts>` from their rows, in **CustomObjectTranslation XSD child
  order**: `caseValues` → `layouts` → `recordTypes` → `validationRules` (fields stay in
  their own files). Group all container-bound rows (object-label + the 3 new types) per
  container path before writing.
- **New global Translations writer**: `write_translations_file(path, labels)` →
  `<Translations><customLabels><name/><label/></customLabels></Translations>`.
- **Routing**: in the `to-files` loop, branch `.objectTranslation-meta.xml` (container) →
  container writer; `.translation-meta.xml` (but not objectTranslation) → translations writer.

### 4. Element shapes (reference)
- recordTypes: `<recordTypes><name>RT</name><label>..</label></recordTypes>`
- validationRules: `<validationRules><name>VR</name><errorMessage>..</errorMessage></validationRules>`
- layouts: `<layouts><layout>Layout Name</layout><sections><label>..</label><section>Sec</section></sections></layouts>`
- customLabels (global): `<customLabels><name>L</name><label>..</label></customLabels>`

### 5. Docs
Update `scripts/python/README.md` (Step 0 retrieval incl. CustomLabels + scoped Layout;
new `type` values; `--labels-list`; manifest note) and `CLAUDE.md` translation section.

## Critical files
- `scripts/python/translation_roundtrip.py` — readers, `build_rows`, `to-files` writers/routing, `--labels-list`
- `manifest/package.xml` — add `CustomLabels`; document scoped `Layout` retrieve
- `scripts/python/README.md`, `CLAUDE.md` — docs
- New: `scripts/python/docs/translation-roadmap.md` (this roadmap)

## Verification (end-to-end)
1. Retrieve with updated manifest + scoped layout command; confirm `recordTypes/`,
   `validationRules/`, `layouts/`, `labels/CustomLabels.labels-meta.xml` present.
2. **Confirm source-format decomposition**: retrieve one object translation that has a record
   type/validation rule (or deploy-convert a sample) to verify they stay inline in the
   container; adjust writer if SFDX decomposes them.
3. `to-csv` on a small field list + `--labels-list`: confirm the 4 new row types appear with
   correct `file`/`source`.
4. Fill translations, `to-files`, then: validate all XML well-formed; confirm container child
   order; confirm `translations/<lang>.translation-meta.xml` has `<customLabels>`.
5. Optional (user runs, not this tooling — never deploy): `sf project deploy start
   --dry-run -o <org>` to validate schema/order against the org.
