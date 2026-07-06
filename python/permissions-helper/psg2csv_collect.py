#!/usr/bin/env python3
"""Collect `sf sday psg2csv` output for many Permission Set Groups into one CSV.

Given a shell-style pattern, find every permissionsetgroup metadata file whose name
matches, run `sf sday psg2csv` for each, combine all output into a single CSV (header
first), then drop rows that are identical apart from the originating PSG -- so the same
Name with different permission flags is preserved, but exact duplicates collapse.

This reads local project metadata only -- it never deploys to the org.

Examples:
  # All AC_EMEA permission set groups, object permissions, default output file
  python3 scripts/python/permissions-helper/psg2csv_collect.py 'AC_EMEA_*'

  # Everything matching *Sales*, field permissions, custom output file
  python3 scripts/python/permissions-helper/psg2csv_collect.py '*Sales*' -r fieldPermissions -o sales_fields.csv
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import subprocess
import sys
from pathlib import Path

META_SUFFIX = ".permissionsetgroup-meta.xml"
DEFAULT_PSG_DIR = Path("force-app/main/default/permissionsetgroups")
_SF = ["cmd", "/c", "sf"] if sys.platform == "win32" else ["sf"]


def find_psg_dir(projectdir: Path | None) -> Path:
    """Locate the permissionsetgroups directory."""
    base = projectdir or Path.cwd()
    candidate = base / DEFAULT_PSG_DIR
    if candidate.is_dir():
        return candidate
    # Fall back to searching the tree for a permissionsetgroups directory.
    for path in base.rglob("permissionsetgroups"):
        if path.is_dir():
            return path
    sys.exit(f"error: could not find a permissionsetgroups directory under {base}")


def match_psgs(psg_dir: Path, pattern: str) -> list[str]:
    """Return developer names of permission set groups matching the pattern.

    The pattern is matched against the file name with and without the metadata
    suffix, so both 'AC_EMEA_*' and 'AC_EMEA_*.permissionsetgroup-meta.xml' work.
    """
    names: list[str] = []
    for file in sorted(psg_dir.glob(f"*{META_SUFFIX}")):
        dev_name = file.name[: -len(META_SUFFIX)]
        if fnmatch.fnmatch(file.name, pattern) or fnmatch.fnmatch(dev_name, pattern):
            names.append(dev_name)
    return names


def run_psg2csv(
    dev_name: str, permission: str, projectdir: Path | None
) -> tuple[str | None, str | None]:
    """Run `sf sday psg2csv` for one PSG.

    Returns (stdout, None) on success or (None, error_message) on failure.
    """
    cmd = _SF + ["sday", "psg2csv", "-p", dev_name, "-r", permission]
    if projectdir is not None:
        cmd += ["-d", str(projectdir)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        return None, f"exit {result.returncode}: {result.stderr.strip()}"
    return result.stdout, None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect sf sday psg2csv output across matching permission set "
        "groups into one CSV, dropping rows that are identical apart from the PSG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "pattern",
        help="Shell-style pattern for permissionsetgroup file names, e.g. 'AC_EMEA_*'.",
    )
    parser.add_argument(
        "-r",
        "--permission",
        choices=["objectPermissions", "fieldPermissions", "userPermissions"],
        default="objectPermissions",
        help="Which permission class to extract (default: objectPermissions).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="psg_combined.csv",
        help="Output CSV file (default: psg_combined.csv).",
    )
    parser.add_argument(
        "-d",
        "--projectdir",
        type=Path,
        default=None,
        help="Project root if not the current directory (passed to psg2csv too).",
    )
    parser.add_argument(
        "--readable-only",
        action="store_true",
        help="Exclude rows whose 'readable' column is not TRUE (fieldPermissions only; "
        "ignored for permission types without a 'readable' column).",
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Skip deduplication and write all rows, including exact duplicates across PSGs.",
    )
    args = parser.parse_args()

    psg_dir = find_psg_dir(args.projectdir)
    dev_names = match_psgs(psg_dir, args.pattern)
    if not dev_names:
        sys.exit(f"error: no permission set groups matched pattern {args.pattern!r} "
                 f"in {psg_dir}")

    total = len(dev_names)
    print(
        f"Matched {total} permission set group(s); extracting {args.permission}...",
        file=sys.stderr,
    )

    # Collect all rows. The header is identical across runs for a given permission
    # type, so capture it once from the first successful run.
    header: list[str] | None = None
    rows: list[list[str]] = []
    for i, dev_name in enumerate(dev_names, 1):
        # Print the progress line before the (slow) sf call so the user can see which
        # PSG is in flight; the result is appended once the call returns.
        print(f"  [{i}/{total}] {dev_name} ... ", end="", flush=True, file=sys.stderr)
        output, err = run_psg2csv(dev_name, args.permission, args.projectdir)
        if output is None:
            print(f"FAILED ({err})", file=sys.stderr)
            continue
        reader = csv.reader(output.splitlines())
        run_rows = list(reader)
        if not run_rows:
            print("no output", file=sys.stderr)
            continue
        if header is None:
            header = run_rows[0]
        data = run_rows[1:]  # skip the per-run header
        rows.extend(data)
        print(f"{len(data)} row(s)", file=sys.stderr)

    if header is None:
        sys.exit("error: no output collected from any permission set group")

    # Optionally drop rows that are not readable (fieldPermissions has a 'readable' column).
    if args.readable_only:
        if "readable" in header:
            readable_idx = header.index("readable")
            before = len(rows)
            rows = [r for r in rows if r and r[readable_idx] == "TRUE"]
            print(
                f"--readable-only: dropped {before - len(rows)} row(s) where readable != TRUE.",
                file=sys.stderr,
            )
        else:
            print(
                f"--readable-only: no 'readable' column in {args.permission} output; "
                "flag ignored.",
                file=sys.stderr,
            )

    if args.keep_duplicates:
        final_rows = [row for row in rows if row]
        with open(args.output, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(final_rows)
        print(
            f"Wrote {len(final_rows)} row(s) to {args.output} (deduplication skipped).",
            file=sys.stderr,
        )
        return

    # De-duplicate while preserving distinct permission combinations. Two rows are
    # considered duplicates only when every column except PSG matches, so the same
    # Name with different permission flags is kept; the originating PSG is ignored
    # for the comparison and the first occurrence wins.
    psg_idx = header.index("PSG") if "PSG" in header else None
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        key = tuple(v for i, v in enumerate(row) if i != psg_idx)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(deduped)

    dupes_removed = len(rows) - len(deduped)
    print(
        f"Wrote {len(deduped)} unique row(s) to {args.output} "
        f"({dupes_removed} duplicate row(s) removed).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
