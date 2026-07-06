#!/usr/bin/env python3
"""End-to-end pipeline: retrieve all metadata and generate a pre-filled translation CSV.

Runs the following steps in order:
  1. gen_manifest.py        — generate manifest/package.xml
  2. sf project retrieve    — retrieve main metadata (objects, value sets, en_US translations)
  3. gen_layout_list.py     — generate layouts-package.xml
  4. sf project retrieve    — retrieve layout metadata
  5. gen_standard_labels.py — generate standard_labels.json (needed for --filter-standard-fields-to-input)
  6. translation_roundtrip.py to-csv — generate the translation CSV

The output CSV has existing translations pre-filled and renamed standard field labels
filtered to only fields that appear in the input.

Steps 3-4 (layouts) are non-fatal: if the referenced objects have no layouts in the org,
a warning is printed and those steps are skipped without aborting.

Examples:
  python3 scripts/python/translation-helpers/gen_translationfile_e2e.py ac_fields.csv es --target-org <alias>
  python3 scripts/python/translation-helpers/gen_translationfile_e2e.py ac_fields.csv es --target-org <alias> -o translations_es.csv
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]
_HERE = Path(__file__).resolve().parent
_WIDTH = 60


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _rule(char: str = "─") -> None:
    print(char * _WIDTH, file=sys.stderr, flush=True)


def run_step(desc: str, cmd: list[str], optional: bool = False) -> bool:
    """Run a command, streaming output to the terminal.

    Returns True on success. On failure: if optional, prints a warning and returns False;
    otherwise aborts the process.
    """
    _rule()
    print(f"[{_now()}]  {desc}", file=sys.stderr)
    print(f"  $ {' '.join(str(c) for c in cmd)}", file=sys.stderr, flush=True)
    print(file=sys.stderr, flush=True)
    t0 = time.monotonic()
    result = subprocess.run(cmd)
    elapsed = time.monotonic() - t0
    print(file=sys.stderr)
    if result.returncode != 0:
        if optional:
            print(
                f"[{_now()}]  SKIPPED  {desc}  (exit {result.returncode}, non-fatal)"
                f"  [{_fmt_elapsed(elapsed)}]",
                file=sys.stderr, flush=True,
            )
            return False
        _rule("═")
        sys.exit(
            f"[{_now()}]  FAILED   {desc}  (exit {result.returncode})"
            f"  [{_fmt_elapsed(elapsed)}]"
        )
    print(
        f"[{_now()}]  done     {desc}  [{_fmt_elapsed(elapsed)}]",
        file=sys.stderr, flush=True,
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end pipeline: retrieve metadata and generate a pre-filled "
        "translation CSV with existing translations and standard fields filtered to input.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_file", metavar="FILE",
                        help="CSV in ac_fields.csv format (Name = Object.Field).")
    parser.add_argument("language", metavar="LANG",
                        help="Target language code(s); comma-separate for a batch, "
                        "e.g. de,fr,es. With multiple, output files are named "
                        "<output stem>_<lang>.csv.")
    parser.add_argument("--target-org", required=True,
                        help="Org alias to retrieve from (required).")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output CSV path (default: translations_<lang>.csv for a single "
                        "language, or translations.csv stem for a batch).")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        sys.exit(f"error: input file not found: {input_path}")

    langs = [s.strip() for s in args.language.split(",") if s.strip()]
    if args.output is None:
        args.output = (
            Path(f"translations_{langs[0]}.csv") if len(langs) == 1
            else Path("translations.csv")
        )

    _rule("═")
    print(
        f"  gen_translationfile_e2e.py\n"
        f"  fields:    {input_path}\n"
        f"  language:  {args.language}\n"
        f"  org:       {args.target_org}\n"
        f"  output:    {args.output}\n"
        f"  started:   {_now()}  (6 steps)",
        file=sys.stderr, flush=True,
    )
    _rule("═")

    py = sys.executable
    t_total = time.monotonic()
    manifest = Path("manifest/package.xml")
    layouts_manifest = Path("layouts-package.xml")
    standard_labels = Path("standard_labels.json")

    # Step 1: generate manifest/package.xml
    run_step(
        "Step 1/6: generate manifest/package.xml",
        [py, str(_HERE / "gen_manifest.py"), str(input_path),
         "--target-org", args.target_org,
         "-o", str(manifest)],
    )

    # Step 2: retrieve main metadata
    run_step(
        "Step 2/6: retrieve main metadata",
        _SF + ["project", "retrieve", "start",
               "-x", str(manifest),
               "-o", args.target_org],
    )

    # Step 3: generate layouts-package.xml (optional — some objects may have no layouts)
    layouts_ok = run_step(
        "Step 3/6: generate layouts-package.xml",
        [py, str(_HERE / "gen_layout_list.py"), str(input_path),
         "--target-org", args.target_org,
         "-o", str(layouts_manifest)],
        optional=True,
    )

    # Step 4: retrieve layout metadata (skipped if step 3 found nothing)
    if layouts_ok:
        run_step(
            "Step 4/6: retrieve layout metadata",
            _SF + ["project", "retrieve", "start",
                   "-x", str(layouts_manifest),
                   "-o", args.target_org],
        )
    else:
        _rule()
        print(f"[{_now()}]  SKIPPED  Step 4/6: retrieve layout metadata  (no layouts found)",
              file=sys.stderr, flush=True)

    # Step 5: generate standard_labels.json (needed for --filter-standard-fields-to-input)
    run_step(
        "Step 5/6: generate standard_labels.json",
        [py, str(_HERE / "gen_standard_labels.py"), str(input_path),
         "--target-org", args.target_org,
         "-o", str(standard_labels)],
    )

    # Step 6: generate translation CSV
    run_step(
        "Step 6/6: generate translation CSV",
        [py, str(_HERE / "translation_roundtrip.py"), "to-csv",
         str(input_path), args.language,
         "--retrieve-existing", "--target-org", args.target_org,
         "--filter-standard-fields-to-input",
         "--standard-labels", str(standard_labels),
         "-o", str(args.output)],
    )

    _rule("═")
    print(
        f"  All steps complete.  Total time: {_fmt_elapsed(time.monotonic() - t_total)}\n"
        f"  Translation CSV: {args.output}",
        file=sys.stderr, flush=True,
    )
    _rule("═")


if __name__ == "__main__":
    main()
