
# This is a work in progress, expect many changes very fast.

# Scripts

Utility scripts for Salesforce DX projects. Clone this repo into the `scripts/` directory of
any SFDX project:

```bash
git clone <url> scripts
```

## What's in here

```
scripts/
├── python/
│   ├── psg2csv_collect.py          — permission analysis (see below)
│   └── translation-helpers/        — translation workflow tools
├── apex/
└── soql/
```

---

## `python/translation-helpers/` — Translation workflow

A set of command-line tools that automate the **Salesforce translation workflow** end-to-end:
from a list of `Object.Field` names to deployable `CustomObjectTranslation`,
`GlobalValueSetTranslation`, `StandardValueSetTranslation`, and `Translations` metadata files.

### What it does

1. **Generates a scoped `package.xml`** from your field list so you retrieve only what you need
   (`gen_manifest.py`)
2. **Builds a translator-ready CSV** with one row per translatable item — custom field labels,
   picklist values, record type names, validation-rule messages, layout section headings, and
   custom labels — with a blank `translation` column (`translation_roundtrip.py to-csv`)
3. **Converts the filled-in CSV back to SFDX source-format metadata** ready for deployment
   (`translation_roundtrip.py to-files`)

Supporting scripts handle edge cases: layout enumeration (`gen_layout_list.py`), standard
picklist value resolution (`gen_standard_picklists.py`), and renamed standard field/object
label resolution (`gen_standard_labels.py`).

### Key design points for developers

- **Read-only against the org.** Every org interaction is a retrieve or describe call. Nothing
  deploys. Generating the output files and deploying them are intentionally separate steps.
- **Offline-first after retrieval.** The CSV build and file generation steps work entirely from
  local metadata — no org connection needed once you've retrieved.
- **Task/Event fields resolve under `Activity`.** The shared `Activity` object translation
  container is handled transparently.
- **Standard field labels via `en_US` CustomObjectTranslation.** Renamed standard labels (e.g.
  `account_currency`) are read from the `<Obj>-en_US` translation file rather than
  `CustomField` metadata, matching how Salesforce actually stores them.
- **Windows-compatible.** All subprocess calls use the `cmd /c sf` shim on Windows; all file
  I/O uses explicit UTF-8 encoding.

### Full usage guide

See [`python/translation-helpers/README.md`](python/translation-helpers/README.md) for the
step-by-step walkthrough, prerequisites, and all command options.

---

## `python/psg2csv_collect.py` — Permission Set Group analysis

Runs `sf sday psg2csv` across every Permission Set Group whose name matches a shell-style
pattern, merges the output into a single CSV, and deduplicates rows that are identical across
groups.

```bash
# Object permissions for all MyPSG* groups
python3 scripts/python/psg2csv_collect.py 'MyPSG*'

# Field permissions
python3 scripts/python/psg2csv_collect.py 'MyPSG*' -r fieldPermissions -o ac_fields.csv
```

Requires the [Sunny Day CLI plugin](https://www.npmjs.com/package/@chasd00/sunny-day):
`sf plugins install @chasd00/sunny-day`. Reads local metadata only — no org connection needed.

---

## Prerequisites

| Requirement | Check | Install |
|---|---|---|
| Salesforce CLI | `sf version` | [developer.salesforce.com/tools/salesforcecli](https://developer.salesforce.com/tools/salesforcecli) |
| Sunny Day plugin | `sf plugins` | `sf plugins install @chasd00/sunny-day` |
| Python 3.9+ | `python3 --version` | [python.org](https://www.python.org/downloads/) |

No additional Python packages are required.
