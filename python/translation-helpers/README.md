# Field Translation & Permission Tools

A set of small command-line tools that help you **translate Salesforce labels into other
languages** without hand-building XML, and (separately) **report on field permissions** from
Permission Set Groups.

This guide is written for a **Salesforce Administrator**. You do not need to be a developer.
You will copy and paste a few commands, send a spreadsheet to a translator, and run one more
command to turn the completed spreadsheet into Salesforce files.

> ## ⛔ The one rule: these tools never change your org
> Every tool here only **reads** from the org and **writes files on your computer**. They
> never deploy. Putting the finished translation files into Salesforce is a separate, normal
> deployment that **you** (or your release process) decide to do.

---

## Quick start

> **Prerequisites:** see [One-time setup](#one-time-setup) if this is your first time running
> these tools.

### 1 — Generate your field list from Permission Set Groups

```bash
python3 scripts/python/permissions-helper/psg2csv_collect.py 'MyPSGs*' -r fieldPermissions -o mypsgs_fields.csv --readable-only
```

Change `'MyPSGs*'` to the pattern that matches your Permission Set Group names (`'*'` for all
groups). This writes `mypsgs_fields.csv` — the field list the translation tools read.

### 2 — Retrieve all metadata and build the translation CSV

```bash
python3 scripts/python/translation-helpers/gen_translationfile_e2e.py mypsgs_fields.csv es --target-org <your-org>
```

This single command runs all six preparation steps in sequence: generates the retrieve
manifests, downloads the org metadata and layouts, builds the standard-labels cache, and
writes `translations_es.csv` with any existing Spanish translations already pre-filled.
Progress is printed step by step with timestamps and elapsed times as it runs.

Change `es` to any Salesforce language code (`de`, `fr`, `pt_BR`, …), or pass several
comma-separated to get one file per language: `es,de,fr`.

### 3 — Send the CSV to your translator

Hand `translations_es.csv` to your translator. They fill in the **`translation` column only**.

### 4 — Turn the completed CSV into deployable files

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-files translations_es.csv --dir out_es
```

### 5 — Deploy (your normal process)

```bash
sf project deploy start -d out_es -o <your-org>
```

This is the only step that changes the org — and it is yours to run, not part of these tools.

For all options (custom labels, multiple languages, include-unchanged standard fields, etc.)
see the [full guide](#how-to-translate-fields-the-main-task) below.

---

## What these tools do for you

Salesforce stores translatable text (field labels, picklist values, record type names,
validation-rule messages, layout headings, custom labels) in many different places and file
formats. Translating it by hand is tedious and error-prone.

These tools automate the boring parts:

1. They **gather everything that needs translating** for the fields you choose into one
   spreadsheet (a CSV).
2. Your translator **fills in the translations** in that one spreadsheet — one column, nothing
   technical.
3. The tools **turn the completed spreadsheet into the exact Salesforce files** needed to
   deploy those translations.

```
   You pick fields  ─►  Tool builds a spreadsheet  ─►  Translator fills it in
                                                              │
   You deploy the files  ◄─  Tool builds Salesforce files  ◄──┘
   (your normal process)
```

---

## One-time setup

You need two things installed, and your org connected. You probably already have these.

| What | Check it's installed | If missing |
|---|---|---|
| **Salesforce CLI** (`sf`) | `sf version` | Install from the [Salesforce CLI page](https://developer.salesforce.com/tools/salesforcecli) |
| **Sunny Day CLI plugin** | `sf plugins` (look for `@chasd00/sunny-day`) | `sf plugins install @chasd00/sunny-day` |
| **Python 3.9 or newer** | `python3 --version` | Install from [python.org](https://www.python.org/downloads/) (macOS usually has it) |

> The Sunny Day plugin adds the `sf sday` commands these tools rely on. Install it once:
>
> ```bash
> sf plugins install @chasd00/sunny-day
> ```
>
> The tools use only what comes with Python — there is nothing extra to `pip install`.

Every command below that touches the org takes an org **alias** (the nickname you gave your
already-connected org) after `-o` or `--target-org`. The examples use the placeholder
**`<your-org>`** — replace it with your own alias everywhere it appears.

Not sure of your alias? List your connected orgs (the `Alias` column) with:

```bash
sf org list
```

Confirm a specific one is connected with:

```bash
sf org display -o <your-org>
```

**Run all commands from the project's top folder** (the one that contains the `manifest` and
`scripts` folders).

---

# How to translate fields (the main task)

Follow these steps in order. The examples translate into **German** (`de`). Salesforce uses
short language codes — for example `de` = German, `fr` = French, `es` = Spanish,
`pt_BR` = Brazilian Portuguese, `ja` = Japanese.

## Step 1 — Make your list of fields to translate

The tools work from a simple spreadsheet listing the fields you want translated. It needs
just **one column named `Name`**, with one `Object.Field` per row. For example, save this as
`ac_fields.csv`:

```
Name
Account.Industry
Account.Type
Widget__c.Color__c
```

> You can create this in Excel or Google Sheets and "Save As CSV". Any CSV with a `Name`
> column works. (If your team already produces a permissions CSV — see
> [Permission analysis](#permission-analysis-optional) below — you can reuse that file here;
> its extra columns are ignored.)

## Step 2 — Generate the manifest and download the org's setup

The tools need a copy of the org's relevant setup on your computer. First, build the
**manifest** — a file that lists exactly what to download for your field list (you don't need
to create it by hand):

```bash
python3 scripts/python/translation-helpers/gen_manifest.py ac_fields.csv --target-org <your-org>
```

This writes `manifest/package.xml`. Then download everything it lists (this only reads from
the org — nothing is changed):

```bash
sf project retrieve start -x manifest/package.xml -o <your-org>
```

Together these download the objects and their fields, picklist value sets, permission sets,
and the existing translations the tools rely on.

Re-run **both** commands when your field list changes or when objects/fields/translations
change in the org.

## Step 3 — Build the translation spreadsheet

This reads your field list and produces a spreadsheet containing every piece of text that
needs translating, with an empty `translation` column for the translator to fill.

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv ac_fields.csv de -o translations_de.csv
```

You now have `translations_de.csv`. Open it to see what will be translated.

**Translate several languages at once** by separating the codes with commas. This writes one
file per language (`translations_de.csv`, `translations_fr.csv`, `translations_es.csv`):

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv ac_fields.csv de,fr,es -o translations.csv
```

**Already translated some of this before?** You can pre-fill the spreadsheet with translations
that already exist in the org, so the translator only fills the gaps. Add
`--retrieve-existing` and your org alias (this only reads from the org):

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv ac_fields.csv de \
    -o translations_de.csv --retrieve-existing --target-org <your-org>
```

See [Optional extras](#optional-extras) for translating record types, validation-rule
messages, layout headings, and custom labels.

## Step 4 — Send the spreadsheet to your translator

Hand `translations_de.csv` to your translator (or translation service). Tell them:

> **Fill in the `translation` column only. Do not change any other column.**

Each row shows the original English text in the `source` column. They type the translated text
in the `translation` column next to it. Rows left blank are simply skipped.

## Step 5 — Turn the completed spreadsheet into Salesforce files

When you get the filled-in spreadsheet back, run:

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-files translations_de.csv --dir out_de
```

This creates a folder `out_de/` containing ready-to-deploy translation files, organized the
way Salesforce expects.

**Have several completed language files?** List them all at once:

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-files \
    translations_de.csv translations_fr.csv translations_es.csv --dir out
```

## Step 6 — Deploy the files (your normal process)

The tools stop here on purpose. To put the translations into Salesforce, deploy the generated
folder the way you normally deploy metadata. For example:

```bash
sf project deploy start -d out_de -o <your-org>
```

This is the **only** step that changes the org, and it's a deliberate action you take outside
these tools. (If someone else handles deployments, just hand them the `out_de` folder.)

**Repeat Steps 3–6 for each language.**

---

## Understanding the translation spreadsheet

The spreadsheet has these columns. **The translator edits only `translation`.**

| Column | What it means |
|---|---|
| `file` | Which Salesforce file this row belongs to (leave as-is). |
| `type` | What kind of item this is — see the table below. |
| `component` | The object or value set it belongs to. |
| `field` | The specific field, record type, rule, etc. |
| `masterLabel` | For picklist/value rows, the specific value. |
| `source` | **The original English text** — what to translate from. |
| `translation` | **The translated text — fill this in.** |

The `type` column tells you what each row is:

| `type` | In plain terms |
|---|---|
| `field-label` | The name (label) of a field. |
| `field-picklist` | A picklist choice on a field. |
| `global-value` | A value from a global value set (shared picklist). |
| `standard-value` | A value from a standard Salesforce picklist. |
| `standard-field` | A standard field whose label your org has renamed. |
| `object-label` | The singular/plural name of an object. |
| `record-type` | A record type's label. |
| `validation-rule` | A validation rule's error message. |
| `layout-section` | A section heading on a page layout. |
| `custom-label` | A Custom Label (Setup → Custom Labels). |

---

## Optional extras

### Record types, validation rules, and layout headings

Record type labels and validation-rule error messages are included **automatically** for the
objects in your field list — no extra steps.

**Layout section headings** need one extra download first, because Salesforce can't filter
layouts by object automatically. This helper finds the layouts for your objects and prepares
the download:

```bash
# 1. Find the relevant layouts and write a download list (reads the org)
python3 scripts/python/translation-helpers/gen_layout_list.py ac_fields.csv --target-org <your-org>

# 2. Download them (reads the org, never deploys)
sf project retrieve start -x layouts-package.xml -o <your-org>
```

After that, Step 3's `to-csv` will include layout heading rows. Without this, those rows are
simply left out.

### Custom Labels

Custom Labels aren't tied to fields, so you list them separately. Put one Custom Label API
name per line in a text file (for example `labels.txt`):

```
My_Greeting
My_Farewell
```

Then add `--labels-list` when building the spreadsheet:

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv ac_fields.csv de \
    -o translations_de.csv --labels-list labels.txt
```

### Trimming extra standard-field rows

By default, the spreadsheet includes **all** renamed standard fields on each object you
reference — which can be more than the fields in your list. The command prints a note when
this happens. To keep only the ones matching your list, first build the `standard_labels.json`
cache (see [Reference data files](#reference-data-files-standard_labelsjson--standard_picklistsjson)),
then add `--filter-standard-fields-to-input`:

```bash
python3 scripts/python/translation-helpers/gen_standard_labels.py ac_fields.csv --target-org <your-org>
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv ac_fields.csv de \
    -o translations_de.csv --filter-standard-fields-to-input
```

You can also just delete the unwanted rows from the spreadsheet by hand.

---

## Reference data files (`standard_labels.json` & `standard_picklists.json`)

These two **optional** files are caches the tools build from the org (using
`sf sobject describe`). You generate each one when you need it — and re-generate it if the
org's standard objects or picklists change. Once generated, the other commands read these
files from your computer and don't need the org again.

Both write their file into the folder you run them from, which is exactly where the tools that
use them look — so there's nothing to configure.

### `standard_labels.json` — used when translating

You only need this for the two advanced translation options:
`--filter-standard-fields-to-input` and `--include-unchanged-standard-fields` (see
[Optional extras](#optional-extras)). It lists every standard field's label so the tool can
match renamed standard fields against your input list.

Generate it (this reads the org):

```bash
python3 scripts/python/translation-helpers/gen_standard_labels.py ac_fields.csv --target-org <your-org>
```

This creates `standard_labels.json`. Step 3's `to-csv` finds it automatically when you use one
of those options.

### `standard_picklists.json` — used in permission analysis

You only need this for `expand_picklists.py` in the
[Permission analysis](#permission-analysis-optional) workflow. It fills in the values of
standard picklists (like `Account.Type`) that aren't included in downloaded metadata. Without
it, `expand_picklists.py` still works but falls back to a smaller built-in list of values.

Generate it (this reads the org):

```bash
python3 scripts/python/translation-helpers/gen_standard_picklists.py ac_fields.csv --target-org <your-org>
```

This creates `standard_picklists.json`, which `expand_picklists.py` picks up automatically.
Instead of a field list, you can also name objects directly:

```bash
python3 scripts/python/translation-helpers/gen_standard_picklists.py --objects Account,Contact,Task --target-org <your-org>
```

---

## Permission analysis (optional)

A separate workflow, unrelated to translation: report which fields and picklist values a set
of **Permission Set Groups** grants. This uses the `sf sday psg2csv` command from the
Sunny Day plugin (see [One-time setup](#one-time-setup)).

```bash
# 1. Collect field permissions for the Permission Set Groups whose names match a pattern
python3 scripts/python/permissions-helper/psg2csv_collect.py 'AC_*' -r fieldPermissions -o ac_fields.csv
#    'AC_*' matches names starting with AC_; use '*' for all.
#    -r can be fieldPermissions, objectPermissions, or userPermissions.

# 2. (optional) Cache standard picklist values — see "Reference data files" above for details
python3 scripts/python/translation-helpers/gen_standard_picklists.py ac_fields.csv --target-org <your-org>

# 3. Expand every picklist field into one row per value
python3 scripts/python/translation-helpers/expand_picklists.py ac_fields.csv -o ac_fields_expanded.csv
```

The `ac_fields.csv` this produces can also be reused as the field list for translation.

---

## Which commands touch the org?

Only these read from the org (they still never deploy):

- `gen_manifest.py` and `sf project retrieve start ...` (Step 2, and the layout download)
- `to-csv ... --retrieve-existing` (the optional pre-fill)
- `gen_layout_list.py`, `gen_standard_picklists.py`, `gen_standard_labels.py`,
  `permissions-helper/psg2csv_collect.py`

Everything else works entirely from the files already on your computer.

The only command that **writes** to the org is `sf project deploy start` in Step 6 — which is
yours to run, not part of these tools.

---

## Command cheat sheet

| Command | What it does |
|---|---|
| `gen_translationfile_e2e.py <fields.csv> <lang> --target-org <your-org>` | **End-to-end:** runs all six preparation steps and writes the translation CSV, pre-filled with existing translations and standard fields filtered to your input. |
| `gen_manifest.py <fields.csv> --target-org <your-org>` | Generate `manifest/package.xml` for your field list (Step 2). |
| `sf project retrieve start -x manifest/package.xml -o <your-org>` | Download the org setup the tools need (Step 2). |
| `translation_roundtrip.py to-csv <fields.csv> <lang> -o <out.csv>` | Build the translation spreadsheet (Step 3). Add `--retrieve-existing --target-org <org>` to pre-fill known translations; `--labels-list <file>` to include Custom Labels. |
| `translation_roundtrip.py to-files <filled.csv> --dir <out>` | Turn the completed spreadsheet into deployable files (Step 5). |
| `gen_layout_list.py <fields.csv> --target-org <your-org>` | Prepare the layout download so layout headings can be translated. |
| `gen_standard_labels.py <fields.csv> --target-org <your-org>` | Build a cache used by `--filter-standard-fields-to-input` and `--include-unchanged-standard-fields`. |
| `gen_standard_picklists.py <fields.csv> --target-org <your-org>` | Cache standard picklist values (for permission analysis). |
| `expand_picklists.py <fields.csv> -o <out.csv>` | Expand picklist fields to one row per value (permission analysis). |
| `permissions-helper/psg2csv_collect.py '<pattern>' -r fieldPermissions -o <out.csv>` | Build a field list from Permission Set Groups. |

Every command has built-in help — add `--help`, for example:

```bash
python3 scripts/python/translation-helpers/translation_roundtrip.py to-csv --help
```

---

## Troubleshooting & good-to-know

- **A field or value isn't in the spreadsheet.** Make sure it's in your `ac_fields.csv`, and
  that you re-ran Step 2 (regenerate the manifest **and** download) recently. The spreadsheet
  only covers what you listed and downloaded.
- **Layout headings are missing.** You must run the two layout-download commands in
  [Optional extras](#optional-extras) before Step 3.
- **Unchanged standard field labels aren't included.** That's intentional — Salesforce
  translates standard labels for you automatically, so only **renamed** standard fields appear.
  (Advanced: `--include-unchanged-standard-fields`, with `gen_standard_labels.py` first, will
  include them, but this is rarely needed.)
- **Re-run Step 2** (regenerate the manifest, then download) whenever your field list changes,
  or when objects, fields, or existing translations change in the org.
- **A translation fails to deploy.** A few standard fields or objects may not be translatable
  in your org. Remove those rows/files and deploy the rest. Languages with grammatical genders
  or cases may need manual tweaks to a few entries.
- **Nothing here ever deploys.** If a command seems like it would change the org, it won't —
  the only change happens when you choose to run the deploy in Step 6.

---

## For maintainers: tests

Offline regression tests run the tools against a small sample under `tests/fixtures/` (no org
needed):

```bash
python3 scripts/python/translation-helpers/tests/run_tests.py
```

They cover picklist expansion and the full translation round-trip (spreadsheet → files),
including record types, validation rules, layout sections, custom labels, the pre-fill of
existing translations, and the layout-list helper. Run them after changing any script.
