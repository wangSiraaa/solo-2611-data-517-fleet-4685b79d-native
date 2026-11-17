#!/usr/bin/env python3
"""Demo / deliverable generator for the 517 task.

Refreshes a copy of materials/517/workbook.zip with
materials/517/source.json and then verifies the result from three
independent angles:

1. the written package is unpacked to disk and its relationships,
   cache dictionary and records are checked against the source file;
2. the slicer selection is shown to follow the typed member identity
   (C), not the old dictionary index (which now points at B);
3. the pivot outputs are compared against aggregates computed
   independently from source.json (no pivotpack code involved).

Finally the written package is re-opened in a *new process* to prove
the on-disk result is self-contained.

Run from the repository root:  python3 scripts/demo_refresh.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pivotpack as pp  # noqa: E402

MATERIALS = os.path.join(ROOT, "materials", "517")
OUT = os.path.join(ROOT, "deliverables")
REFRESHED = os.path.join(OUT, "workbook.refreshed.zip")
UNPACKED = os.path.join(OUT, "unpacked")

LINES = []


def say(text=""):
    print(text)
    LINES.append(text)


def check(label, ok, detail=""):
    mark = "OK  " if ok else "FAIL"
    say(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"verification failed: {label}")


def independent_aggregate(source_path, select_region):
    """Compute the expected pivot cells straight from source.json.

    Uses nothing but json + Decimal: filter rows by the slicer's typed
    selection on 'region', group by region, aggregate 'amount'.
    """
    with open(source_path, "rb") as fh:
        data = json.loads(fh.read(), parse_float=Decimal, parse_int=Decimal)
    col_region = data["columns"].index("region")
    col_amount = data["columns"].index("amount")
    sums, counts = {}, {}
    for row in data["rows"]:
        region, amount = row[col_region], row[col_amount]
        if region != select_region:            # slicer: typed identity 's':C
            continue
        if amount is not None:                 # sum ignores null; count skips it
            sums[region] = sums.get(region, Decimal(0)) + amount
            counts[region] = counts.get(region, 0) + 1
        else:
            sums.setdefault(region, Decimal(0))
            counts.setdefault(region, 0)
    fmt = lambda d: {k: pp.canon_decimal(v) for k, v in d.items()}
    return fmt(sums), {k: str(v) for k, v in counts.items()}


def main():
    os.makedirs(OUT, exist_ok=True)
    source = os.path.join(MATERIALS, "source.json")

    say("=" * 72)
    say("1. refresh: old dictionary [B,C] with C selected, new source adds A")
    say("   and swaps the column order (amount, region)")
    say("=" * 72)
    shutil.copyfile(os.path.join(MATERIALS, "workbook.zip"), REFRESHED)
    wb = pp.Workbook.open(REFRESHED)
    say(f"before: fields={wb.get_cache('sales-cache').field_names()} "
        f"selection={wb.slicer_state('region').selected}")
    report = wb.refresh_cache("sales-cache", source)
    say(report.summary())
    say(f"written: {os.path.relpath(REFRESHED, ROOT)}")

    say()
    say("=" * 72)
    say("2. unpack the refreshed package and verify relationships + records")
    say("=" * 72)
    if os.path.isdir(UNPACKED):
        shutil.rmtree(UNPACKED)
    with zipfile.ZipFile(REFRESHED) as zf:
        zf.extractall(UNPACKED)
    for name in sorted(os.path.relpath(os.path.join(d, f), UNPACKED)
                       for d, _, files in os.walk(UNPACKED) for f in files):
        say(f"  unpacked/{name}")

    # -- relationships: identical identity to the original package
    with zipfile.ZipFile(os.path.join(MATERIALS, "workbook.zip")) as zf:
        old_rels = zf.read("_rels/package.xml")
    with open(os.path.join(UNPACKED, "_rels", "package.xml"), "rb") as fh:
        new_rels = fh.read()
    check("relationships part byte-identical to the original", old_rels == new_rels)
    for rel in wb._package.rels:
        say(f"    rel id={rel.id!r} kind={rel.kind!r} target={rel.target!r}")

    # -- cache read independently straight from the unpacked part bytes
    with open(os.path.join(UNPACKED, "parts", "cx.xml"), "rb") as fh:
        cache = pp.Cache.from_part(fh.read())   # Cache used without any Workbook
    say(f"  cache id={cache.id!r} fields={cache.field_names()}")
    for f in cache.fields:
        say(f"    dictionary {f.name}: {[pp.member_kind_v(m) for m in f.members]}")
    with open(source, "rb") as fh:
        src = json.loads(fh.read(), parse_float=Decimal, parse_int=Decimal)
    expected_records = [tuple(pp.member_from_value(v) for v in row) for row in src["rows"]]
    check("cache_id preserved", cache.id == "sales-cache")
    check("fields follow the source column order",
          cache.field_names() == src["columns"], str(cache.field_names()))
    check("records equal the source rows (typed, in order)",
          cache.read_records() == expected_records,
          f"{len(expected_records)} records")
    with open(source, "rb") as fh:
        check("source_sha256 == sha256(source.json bytes)",
              cache.source_sha256 == hashlib.sha256(fh.read()).hexdigest(),
              cache.source_sha256[:16] + "…")

    # -- opaque part untouched
    with zipfile.ZipFile(os.path.join(MATERIALS, "workbook.zip")) as zf:
        old_opaque = zf.read("opaque/vendor.bin")
    with open(os.path.join(UNPACKED, "opaque", "vendor.bin"), "rb") as fh:
        check("opaque part byte-identical", fh.read() == old_opaque)

    say()
    say("=" * 72)
    say("3. selection identity: still C, not the B now sitting at old index 1")
    say("=" * 72)
    state = wb.slicer_state("region")
    region_field = cache.fields[cache.field_position("region")]
    say(f"  new region dictionary: {[pp.member_kind_v(m) for m in region_field.members]}")
    say(f"  old index 1 would now resolve to: "
        f"{pp.member_kind_v(region_field.members[1])}")
    say(f"  stored selection: {[pp.member_kind_v(m) + (('missing',) if miss else ()) for m, miss in state.items]}")
    check("selected member is ('s','C')", state.selected == [("s", "C")])
    check("selection is not missing", state.items == [(("s", "C"), False)])
    check("slicer field position migrated to the new column order",
          wb._slicers[state.rel_id].field == cache.field_position("region"))

    say()
    say("=" * 72)
    say("4. pivot outputs vs. aggregates computed independently from source.json")
    say("=" * 72)
    exp_sum, exp_count = independent_aggregate(source, "C")
    say(f"  independent (source.json, region=='C'): sum={exp_sum} count={exp_count}")
    for pid, expected in (("sales", exp_sum), ("count", exp_count)):
        result = wb.read_pivot(pid)
        got = {}
        for cell in result.cells:
            row_member = cell.row_key[0]
            assert row_member[0] == "s"
            got[row_member[1]] = cell.value
        say(f"  pivot {pid!r} ({result.aggregate} of {result.measure_field} "
            f"by {result.row_field}): {got}")
        check(f"pivot {pid!r} matches the independent computation", got == expected)
    with open(os.path.join(MATERIALS, "samples.json"), encoding="utf-8") as fh:
        samples = json.load(fh)
    check("samples.json: expected_selected",
          state.selected == [("s", samples["expected_selected"])])
    check("samples.json: expected_sum",
          exp_sum == {samples["expected_selected"]: samples["expected_sum"]})

    say()
    say("=" * 72)
    say("5. re-read the written package in a new process")
    say("=" * 72)
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import pivotpack as pp;"
        "wb = pp.Workbook.open(%r);"
        "print('selection:', wb.slicer_state('region').selected);"
        "print('sales:', wb.read_pivot('sales').values());"
        "print('count:', wb.read_pivot('count').values());"
        "print('sha:', wb.get_cache('sales-cache').source_sha256)"
        % (ROOT, REFRESHED)
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True)
    say("  " + proc.stdout.strip().replace("\n", "\n  "))
    check("new process re-read the package", proc.returncode == 0, proc.stderr.strip())

    report_path = os.path.join(OUT, "VERIFICATION.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")
    print(f"\nverification report written to {os.path.relpath(report_path, ROOT)}")


if __name__ == "__main__":
    main()
