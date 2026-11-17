"""End-to-end and unit tests for pivotpack.

Run from the repository root:  python -m unittest discover -v
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import warnings
import zipfile
from decimal import Decimal

import pivotpack as pp
from pivotpack import Cache, Workbook
from pivotpack.errors import (
    MeasureError,
    PackageError,
    SourceShapeError,
    UnknownCacheError,
)

from .helpers import (
    build_package,
    cache_xml,
    member_xml,
    pivot_xml,
    rels_xml,
    slicer_xml,
    write_source,
    write_source_raw,
)

MATERIALS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "materials", "517")

SHA_OLD = "34290dde424d6cfdb41f2b943c12ad7175bc436a6ff3f143207d66dff6c598e2"


def key(*members):
    return pp.axis_key_json(members)


def sales_parts(selected=("C",), cells_sales=(), cells_count=()):
    """The hand-built equivalent of the materials workbook parts."""
    cache = cache_xml(
        "sales-cache", SHA_OLD,
        [("region", [member_xml("s", "B"), member_xml("s", "C")]),
         ("amount", [member_xml("n", "5"), member_xml("n", "7")])],
        [(0, 0), (1, 1)],
    )
    sales = pivot_xml("sales", "cache-main", 0, "", 1, "sum", cells=cells_sales)
    count = pivot_xml("count", "cache-main", 0, "", 1, "count", cells=cells_count)
    slicer = slicer_xml("region", "cache-main", 0, "explicit",
                        items=[("s", v, 0) for v in selected],
                        targets=["sales-pivot", "count-pivot"])
    parts = [("parts/cx.xml", cache), ("reports/p7.xml", sales),
             ("reports/p8.xml", count), ("controls/region.xml", slicer)]
    rels = [("cache-main", "cache", "parts/cx.xml"),
            ("sales-pivot", "pivot", "reports/p7.xml"),
            ("count-pivot", "pivot", "reports/p8.xml"),
            ("region-choice", "slicer", "controls/region.xml")]
    return parts, rels


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pivotpack-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def make_sales_workbook(self, name="wb.zip", **kw):
        parts, rels = sales_parts(**kw)
        extras = [("opaque/vendor.bin", b"opaque retain\xc3\xa9\n")]
        return build_package(self.path(name), parts, rels, extras)


# ----------------------------------------------------------------------
# the demo scenario
# ----------------------------------------------------------------------
class TestDemoScenario(Base):
    def test_selection_follows_identity_not_index(self):
        """Old dictionary [B,C] with C selected; new dictionary [A,B,C].
        The selection must stay C, not the B now sitting at old index 1."""
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        report = wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))

        cache = wb.get_cache("sales-cache")
        self.assertEqual(cache.id, "sales-cache")            # cache_id preserved
        self.assertEqual(cache.field_names(), ["amount", "region"])  # source order
        self.assertEqual([m for m in cache.fields[1].members],
                         [("s", "A"), ("s", "B"), ("s", "C")])
        # old index 1 in the new dictionary would be B; the selection is C
        state = wb.slicer_state("region")
        self.assertEqual(state.selected, [("s", "C")])
        self.assertEqual(state.items, [(("s", "C"), False)])
        self.assertEqual(state.field_name, "region")

        sales = wb.read_pivot("sales")
        self.assertEqual(sales.row_field, "region")
        self.assertEqual(sales.measure_field, "amount")
        self.assertEqual(sales.values(), {(key(("s", "C")), "[]"): "7"})
        count = wb.read_pivot("count")
        self.assertEqual(count.values(), {(key(("s", "C")), "[]"): "1"})

        # provenance digest of the new source file bytes
        with open(os.path.join(MATERIALS, "source.json"), "rb") as fh:
            self.assertEqual(cache.source_sha256, hashlib.sha256(fh.read()).hexdigest())
        self.assertEqual(report.record_count, 3)

    def test_samples_json_conformance(self):
        with open(os.path.join(MATERIALS, "samples.json"), encoding="utf-8") as fh:
            samples = json.load(fh)
        self.assertEqual(samples["old_members"], ["B", "C"])
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        self.assertEqual(wb.slicer_state("region").selected,
                         [("s", samples["expected_selected"])])
        sales = wb.read_pivot("sales")
        self.assertEqual(list(sales.values().values()), [samples["expected_sum"]])

    def test_records_match_source_rows(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        cache = wb.get_cache("sales-cache")
        self.assertEqual(
            cache.read_records(),
            [(("n", Decimal(3)), ("s", "A")),
             (("n", Decimal(5)), ("s", "B")),
             (("n", Decimal(7)), ("s", "C"))],
        )

    def test_unrelated_parts_byte_preserved(self):
        wb_path = self.make_sales_workbook()
        before = {}
        with zipfile.ZipFile(wb_path) as zf:
            for name in zf.namelist():
                before[name] = zf.read(name)
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        with zipfile.ZipFile(wb_path) as zf:
            after = {name: zf.read(name) for name in zf.namelist()}
        self.assertEqual(before["opaque/vendor.bin"], after["opaque/vendor.bin"])
        self.assertEqual(before["_rels/package.xml"], after["_rels/package.xml"])
        self.assertNotEqual(before["parts/cx.xml"], after["parts/cx.xml"])

    def test_relationship_identity_preserved(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        rels = [(r.id, r.kind, r.target) for r in wb._package.rels]
        self.assertEqual(rels, [
            ("cache-main", "cache", "parts/cx.xml"),
            ("sales-pivot", "pivot", "reports/p7.xml"),
            ("count-pivot", "pivot", "reports/p8.xml"),
            ("region-choice", "slicer", "controls/region.xml"),
        ])


# ----------------------------------------------------------------------
# typed identities
# ----------------------------------------------------------------------
class TestTypedIdentities(Base):
    def test_mixed_types_are_distinct_members(self):
        """Text "1", number 1 and bool true are three different members;
        1 and 1.0 are the same one; null is its own member."""
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, "", 1, "count")
        parts = [("c.xml", cache), ("p.xml", pivot)]
        rels = [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")]
        wb_path = build_package(self.path("wb.zip"), parts, rels)

        write_source(self.path("src.json"), ["f", "g"],
                     [["1", 1], [1, 2], [1.0, 3], [True, 4], [None, 5], [2.5, 6]])
        wb = Workbook.open(wb_path)
        wb.refresh_cache("c", self.path("src.json"))

        members = wb.get_cache("c").fields[0].members
        self.assertEqual(members, [("s", "1"), ("n", Decimal(1)), ("b", True),
                                   ("m", None), ("n", Decimal("2.5"))])
        result = wb.read_pivot("p")
        self.assertEqual(set(result.values()), {
            (key(("s", "1")), "[]"), (key(("n", Decimal(1))), "[]"),
            (key(("b", True)), "[]"), (key(("m", None)), "[]"),
            (key(("n", Decimal("2.5"))), "[]"),
        })
        # count for the numeric-1 group is 2 (rows 1 and 1.0 collapsed)
        self.assertEqual(result.values()[(key(("n", Decimal(1))), "[]")], "2")

    def test_decimal_exactness(self):
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum")
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        write_source(self.path("src.json"), ["f", "g"],
                     [["A", 0.1], ["A", 0.2], ["B", 1.10], ["B", 2.5]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("src.json"))
        values = wb.read_pivot("p").values()
        self.assertEqual(values[(key(("s", "A")), "[]")], "0.3")   # exact decimal
        self.assertEqual(values[(key(("s", "B")), "[]")], "3.6")

    def test_unicode_and_special_chars_roundtrip(self):
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum")
        slicer = slicer_xml("s", "cr", 0, "all", targets=["pr"])
        build_package(self.path("wb.zip"),
                      [("c.xml", cache), ("p.xml", pivot), ("s.xml", slicer)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml"),
                       ("sr", "slicer", "s.xml")])
        tricky = ['华北 "东区" <A&B>', "emoji 🚀", "line\nbreak"]
        write_source(self.path("src.json"), ["f", "g"],
                     [[tricky[0], 1], [tricky[1], 2], [tricky[2], 3]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("src.json"))
        values = wb.read_pivot("p").values()
        for i, name in enumerate(tricky, 1):
            self.assertEqual(values[(key(("s", name)), "[]")], str(i))
        # re-open from disk: identical axis identities
        wb2 = Workbook.open(self.path("wb.zip"))
        self.assertEqual(wb2.read_pivot("p").values(), values)
        # selection with the same tricky identities survives the roundtrip
        wb2.set_selection("s", [tricky[0]])
        self.assertEqual(wb2.slicer_state("s").selected, [("s", tricky[0])])
        self.assertEqual(Workbook.open(self.path("wb.zip")).read_pivot("p").values(),
                         {(key(("s", tricky[0])), "[]"): "1"})

    def test_sum_exact_beyond_default_decimal_context(self):
        """Sums must stay exact even past the 28-digit default context."""
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum")
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        write_source_raw(self.path("src.json"),
                         '{"columns": ["f", "g"], "rows": ['
                         '["A", 0.123456789012345678901234567890],'
                         '["A", 0.000000000000000000000000000001],'
                         '["A", 100000000000000000000000000000]]}')
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("src.json"))
        values = wb.read_pivot("p").values()
        self.assertEqual(values[(key(("s", "A")), "[]")],
                         "100000000000000000000000000000.123456789012345678901234567891")

    def test_coerce_member_forms(self):
        self.assertEqual(pp.coerce_member(True), ("b", True))
        self.assertEqual(pp.coerce_member(1), ("n", Decimal(1)))
        self.assertEqual(pp.coerce_member(1.5), ("n", Decimal("1.5")))
        self.assertEqual(pp.coerce_member("x"), ("s", "x"))
        self.assertEqual(pp.coerce_member(None), ("m", None))
        self.assertEqual(pp.coerce_member(("n", 1)), ("n", Decimal(1)))
        self.assertEqual(pp.coerce_member(("b", True)), ("b", True))
        self.assertEqual(pp.coerce_member({"kind": "s", "v": "C"}), ("s", "C"))
        with self.assertRaises(pp.PivotPackError):
            pp.coerce_member(("n", "not-a-number"))
        with self.assertRaises(pp.PivotPackError):
            pp.coerce_member([1, 2])


# ----------------------------------------------------------------------
# slicer semantics
# ----------------------------------------------------------------------
class TestSlicerSemantics(Base):
    def test_member_disappear_and_reappear(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        # C disappears from the source
        write_source(self.path("s1.json"), ["amount", "region"], [[3, "A"], [5, "B"]])
        wb.refresh_cache("sales-cache", self.path("s1.json"))
        state = wb.slicer_state("region")
        self.assertEqual(state.items, [(("s", "C"), True)])  # kept, flagged missing
        # missing selection filters to zero rows -- it must NOT become "all"
        self.assertEqual(wb.read_pivot("sales").cells, [])
        self.assertEqual(wb.read_pivot("count").cells, [])
        # C comes back: the same selection identity matches again
        write_source(self.path("s2.json"), ["amount", "region"],
                     [[3, "A"], [5, "B"], [7, "C"]])
        wb.refresh_cache("sales-cache", self.path("s2.json"))
        state = wb.slicer_state("region")
        self.assertEqual(state.items, [(("s", "C"), False)])
        self.assertEqual(wb.read_pivot("sales").values(), {(key(("s", "C")), "[]"): "7"})

    def test_empty_selection_is_not_select_all(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))

        wb.set_selection("region", [])          # explicit empty -> zero rows
        state = wb.slicer_state("region")
        self.assertEqual(state.mode, "explicit")
        self.assertEqual(state.items, [])
        self.assertEqual(wb.read_pivot("sales").cells, [])

        wb.set_selection("region", mode="all")  # select all -> every row
        self.assertEqual(wb.slicer_state("region").mode, "all")
        values = wb.read_pivot("sales").values()
        self.assertEqual(values, {
            (key(("s", "A")), "[]"): "3",
            (key(("s", "B")), "[]"): "5",
            (key(("s", "C")), "[]"): "7",
        })

        wb.set_selection("region", ["A", "C"])  # explicit subset
        values = wb.read_pivot("sales").values()
        self.assertEqual(values, {(key(("s", "A")), "[]"): "3",
                                  (key(("s", "C")), "[]"): "7"})

    def test_select_all_does_not_freeze_member_list(self):
        """mode=all must cover members that appear only later."""
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.set_selection("region", mode="all")
        write_source(self.path("s.json"), ["amount", "region"],
                     [[1, "A"], [2, "B"], [3, "C"], [4, "D"]])
        wb.refresh_cache("sales-cache", self.path("s.json"))
        self.assertEqual(wb.slicer_state("region").mode, "all")
        self.assertEqual(len(wb.read_pivot("sales").cells), 4)  # D included

    def test_set_selection_of_missing_member_then_reappear(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        wb.set_selection("region", ["Z"])       # not in the dictionary
        self.assertEqual(wb.slicer_state("region").items, [(("s", "Z"), True)])
        self.assertEqual(wb.read_pivot("sales").cells, [])
        write_source(self.path("s.json"), ["amount", "region"], [[9, "Z"], [1, "A"]])
        wb.refresh_cache("sales-cache", self.path("s.json"))
        self.assertEqual(wb.slicer_state("region").items, [(("s", "Z"), False)])
        self.assertEqual(wb.read_pivot("sales").values(), {(key(("s", "Z")), "[]"): "9"})

    def test_slicer_affects_only_its_targets(self):
        """Associations come from the package, not from names."""
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        p1 = pivot_xml("region", "cr", 0, "", 1, "sum")    # misleading names
        p2 = pivot_xml("count", "cr", 0, "", 1, "sum")
        slicer = slicer_xml("amount", "cr", 0, "explicit",
                            items=[("s", "A", 0)], targets=["p1-rel"])
        parts = [("c.xml", cache), ("p1.xml", p1), ("p2.xml", p2), ("s.xml", slicer)]
        rels = [("cr", "cache", "c.xml"), ("p1-rel", "pivot", "p1.xml"),
                ("p2-rel", "pivot", "p2.xml"), ("s-rel", "slicer", "s.xml")]
        build_package(self.path("wb.zip"), parts, rels)
        write_source(self.path("s.json"), ["f", "g"], [["A", 1], ["B", 2]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        self.assertEqual(wb.read_pivot("region").values(),
                         {(key(("s", "A")), "[]"): "1"})          # filtered
        self.assertEqual(wb.read_pivot("count").values(),
                         {(key(("s", "A")), "[]"): "1",
                          (key(("s", "B")), "[]"): "2"})          # untouched


# ----------------------------------------------------------------------
# aggregation semantics
# ----------------------------------------------------------------------
class TestAggregation(Base):
    def _build(self, aggregates=("sum", "count")):
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        parts = [("c.xml", cache)]
        rels = [("cr", "cache", "c.xml")]
        for i, agg in enumerate(aggregates):
            parts.append((f"p{i}.xml", pivot_xml(f"p-{agg}", "cr", 0, "", 1, agg)))
            rels.append((f"p{i}-rel", "pivot", f"p{i}.xml"))
        return build_package(self.path("wb.zip"), parts, rels)

    def test_null_handling_and_zero_vs_missing(self):
        self._build()
        write_source(self.path("s.json"), ["f", "g"],
                     [["A", None], ["A", 5], ["B", 5], ["B", -5], [None, 9]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        sums = wb.read_pivot("p-sum").values()
        self.assertEqual(sums[(key(("s", "A")), "[]")], "5")   # null ignored
        self.assertEqual(sums[(key(("s", "B")), "[]")], "0")   # real zero sum: cell exists
        self.assertEqual(sums[(key(("m", None)), "[]")], "9")  # null axis member
        self.assertNotIn((key(("s", "C")), "[]"), sums)        # missing combo: no cell
        counts = wb.read_pivot("p-count").values()
        self.assertEqual(counts[(key(("s", "A")), "[]")], "1")  # count skips null
        self.assertEqual(counts[(key(("s", "B")), "[]")], "2")

    def test_empty_data_is_legal(self):
        self._build()
        write_source(self.path("s.json"), ["f", "g"], [])
        wb = Workbook.open(self.path("wb.zip"))
        report = wb.refresh_cache("c", self.path("s.json"))
        self.assertEqual(report.record_count, 0)
        cache = wb.get_cache("c")
        self.assertEqual(cache.field_names(), ["f", "g"])   # full field definitions
        self.assertEqual(cache.read_records(), [])
        self.assertEqual(wb.read_pivot("p-sum").cells, [])
        # re-open from disk: still valid
        wb2 = Workbook.open(self.path("wb.zip"))
        self.assertEqual(wb2.read_pivot("p-count").cells, [])

    def test_column_axis(self):
        cache = cache_xml("c", "x", [("r", []), ("k", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, 1, 2, "sum")
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        write_source(self.path("s.json"), ["r", "k", "g"],
                     [["A", "x", 1], ["A", "y", 2], ["A", "x", 3], ["B", "x", 4]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        result = wb.read_pivot("p")
        self.assertEqual(result.column_field, "k")
        self.assertEqual(result.values(), {
            (key(("s", "A")), key(("s", "x"))): "4",
            (key(("s", "A")), key(("s", "y"))): "2",
            (key(("s", "B")), key(("s", "x"))): "4",
        })

    def test_pivot_field_filter(self):
        cache = cache_xml("c", "x",
                          [("f", [member_xml("s", "A"), member_xml("s", "B")]),
                           ("g", [member_xml("n", "1"), member_xml("n", "2")])],
                          [(0, 0), (1, 1)])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum", filters=[(0, [1])])
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        # new dictionary reorders members: filter on B must follow the identity
        write_source(self.path("s.json"), ["g", "f"], [[1, "B"], [2, "A"], [3, "B"]])
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        self.assertEqual(wb.read_pivot("p").values(), {(key(("s", "B")), "[]"): "4"})

    def test_pivot_filter_member_vanishes(self):
        cache = cache_xml("c", "x",
                          [("f", [member_xml("s", "A"), member_xml("s", "B")]),
                           ("g", [member_xml("n", "1")])],
                          [(0, 0), (1, 0)])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum", filters=[(0, [1])])
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        write_source(self.path("s.json"), ["f", "g"], [["A", 1]])  # B gone
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        # the filter's only member vanished -> explicit empty -> zero rows
        self.assertEqual(wb.read_pivot("p").cells, [])

    def test_shared_cache_different_axes_one_refresh(self):
        cache = cache_xml("c", "x", [("region", []), ("category", []), ("amount", [])], [])
        p_region = pivot_xml("by-region", "cr", 0, "", 2, "sum")
        p_category = pivot_xml("by-category", "cr", 1, "", 2, "sum")
        parts = [("c.xml", cache), ("p1.xml", p_region), ("p2.xml", p_category)]
        rels = [("cr", "cache", "c.xml"), ("p1", "pivot", "p1.xml"),
                ("p2", "pivot", "p2.xml")]
        build_package(self.path("wb.zip"), parts, rels)
        rows = [["N", "apple", 1], ["S", "apple", 2], ["N", "pear", 3]]
        write_source(self.path("s.json"), ["region", "category", "amount"], rows)
        wb = Workbook.open(self.path("wb.zip"))
        wb.refresh_cache("c", self.path("s.json"))
        # both reports consumed the same batch of new records
        self.assertEqual(wb.read_pivot("by-region").values(),
                         {(key(("s", "N")), "[]"): "4", (key(("s", "S")), "[]"): "2"})
        self.assertEqual(wb.read_pivot("by-category").values(),
                         {(key(("s", "apple")), "[]"): "3", (key(("s", "pear")), "[]"): "3"})


# ----------------------------------------------------------------------
# atomicity and failure handling
# ----------------------------------------------------------------------
class TestFailureAtomicity(Base):
    def assert_package_untouched(self, wb_path, before_bytes):
        with open(wb_path, "rb") as fh:
            self.assertEqual(fh.read(), before_bytes)
        wb = Workbook.open(wb_path)   # old package still fully readable
        self.assertEqual(wb.slicer_state("region").selected, [("s", "C")])
        self.assertEqual(wb.read_pivot("sales").cells, [])
        self.assertEqual(wb.read_pivot("count").cells, [])

    def test_source_shape_errors_keep_old_package(self):
        wb_path = self.make_sales_workbook()
        with open(wb_path, "rb") as fh:
            before = fh.read()

        # wrong column set
        write_source(self.path("bad1.json"), ["amount", "town"], [[1, "A"]])
        # row arity mismatch
        write_source(self.path("bad2.json"), ["amount", "region"], [[1, "A", 9]])
        # non-finite number literal
        write_source_raw(self.path("bad3.json"),
                         '{"columns": ["amount", "region"], "rows": [[NaN, "A"]]}')
        # unsupported value type
        write_source_raw(self.path("bad4.json"),
                         '{"columns": ["amount", "region"], "rows": [[[1], "A"]]}')
        # not even the right JSON shape
        write_source_raw(self.path("bad5.json"), '{"columns": ["amount"]}')

        wb = Workbook.open(wb_path)
        for name in ("bad1.json", "bad2.json", "bad3.json", "bad4.json", "bad5.json"):
            with self.assertRaises(SourceShapeError, msg=name):
                wb.refresh_cache("sales-cache", self.path(name))
            self.assert_package_untouched(wb_path, before)
        with self.assertRaises(UnknownCacheError):
            wb.refresh_cache("no-such-cache", self.path("bad1.json"))

    def test_measure_error_keeps_old_package_and_all_reports(self):
        """A text measure consumed by the pivots aborts the whole refresh:
        neither of the two reports may be updated."""
        wb_path = self.make_sales_workbook()
        with open(wb_path, "rb") as fh:
            before = fh.read()
        write_source(self.path("bad.json"), ["amount", "region"],
                     [[3, "A"], ["oops", "B"], [7, "C"]])
        wb = Workbook.open(wb_path)
        with self.assertRaises(MeasureError):
            wb.refresh_cache("sales-cache", self.path("bad.json"))
        self.assert_package_untouched(wb_path, before)

    def test_measure_error_also_for_count(self):
        cache = cache_xml("c", "x", [("f", []), ("g", [])], [])
        pivot = pivot_xml("p", "cr", 0, "", 1, "count")
        build_package(self.path("wb.zip"), [("c.xml", cache), ("p.xml", pivot)],
                      [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])
        write_source(self.path("s.json"), ["f", "g"], [["A", "text"]])
        wb = Workbook.open(self.path("wb.zip"))
        with self.assertRaises(MeasureError):
            wb.refresh_cache("c", self.path("s.json"))

    def test_measure_contract_is_column_level(self):
        """A non-numeric measure value blocks the refresh even in a record
        the slicer currently excludes: the measure column is a data
        contract, independent of the transient selection state."""
        wb_path = self.make_sales_workbook()  # slicer selects C only
        write_source(self.path("s.json"), ["amount", "region"],
                     [["oops", "A"], [7, "C"]])
        wb = Workbook.open(wb_path)
        with self.assertRaises(MeasureError):
            wb.refresh_cache("sales-cache", self.path("s.json"))
        # old package still readable, old results intact
        self.assertEqual(wb.read_pivot("sales").cells, [])


# ----------------------------------------------------------------------
# illegal packages
# ----------------------------------------------------------------------
class TestIllegalPackages(Base):
    def assert_illegal(self, parts, rels, extras=(), msg=""):
        build_package(self.path("bad.zip"), parts, rels, extras)
        with self.assertRaises(PackageError, msg=msg):
            Workbook.open(self.path("bad.zip"))

    def test_duplicate_member_identity(self):
        cache = cache_xml("c", "x", [("f", [member_xml("n", "1"),
                                            member_xml("n", "1.0")])], [])
        self.assert_illegal([("c.xml", cache)], [("cr", "cache", "c.xml")],
                            msg="1 and 1.0 are the same identity")

    def test_record_index_out_of_range(self):
        cache = cache_xml("c", "x", [("f", [member_xml("s", "A")])], [(1,)])
        self.assert_illegal([("c.xml", cache)], [("cr", "cache", "c.xml")])

    def test_record_arity_mismatch(self):
        cache = cache_xml("c", "x", [("f", [member_xml("s", "A")]),
                                     ("g", [member_xml("n", "1")])], [(0,)])
        self.assert_illegal([("c.xml", cache)], [("cr", "cache", "c.xml")])

    def test_pivot_field_out_of_range(self):
        cache = cache_xml("c", "x", [("f", [member_xml("s", "A")])], [(0,)])
        pivot = pivot_xml("p", "cr", 3, "", 0, "sum")
        self.assert_illegal([("c.xml", cache), ("p.xml", pivot)],
                            [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])

    def test_filter_index_out_of_range(self):
        cache = cache_xml("c", "x", [("f", [member_xml("s", "A")]),
                                     ("g", [member_xml("n", "1")])], [(0, 0)])
        pivot = pivot_xml("p", "cr", 0, "", 1, "sum", filters=[(0, [5])])
        self.assert_illegal([("c.xml", cache), ("p.xml", pivot)],
                            [("cr", "cache", "c.xml"), ("pr", "pivot", "p.xml")])

    def test_slicer_target_unknown(self):
        cache = cache_xml("c", "x", [("f", [member_xml("s", "A")])], [(0,)])
        slicer = slicer_xml("s", "cr", 0, "explicit", targets=["nope"])
        self.assert_illegal([("c.xml", cache), ("s.xml", slicer)],
                            [("cr", "cache", "c.xml"), ("sr", "slicer", "s.xml")])

    def test_slicer_cross_cache_target(self):
        c1 = cache_xml("c1", "x", [("f", [member_xml("s", "A")])], [(0,)])
        c2 = cache_xml("c2", "x", [("f", [member_xml("s", "A")])], [(0,)])
        pivot = pivot_xml("p", "c2r", 0, "", 0, "sum")
        slicer = slicer_xml("s", "c1r", 0, "explicit", targets=["pr"])
        self.assert_illegal(
            [("c1.xml", c1), ("c2.xml", c2), ("p.xml", pivot), ("s.xml", slicer)],
            [("c1r", "cache", "c1.xml"), ("c2r", "cache", "c2.xml"),
             ("pr", "pivot", "p.xml"), ("sr", "slicer", "s.xml")])

    def test_duplicate_rel_id(self):
        cache = cache_xml("c", "x", [("f", [])], [])
        self.assert_illegal([("c.xml", cache)],
                            [("cr", "cache", "c.xml"), ("cr", "cache", "c.xml")])

    def test_unknown_rel_kind(self):
        cache = cache_xml("c", "x", [("f", [])], [])
        self.assert_illegal([("c.xml", cache)], [("cr", "sheet", "c.xml")])

    def test_missing_rel_target(self):
        self.assert_illegal([], [("cr", "cache", "nope.xml")])

    def test_duplicate_zip_entries(self):
        with zipfile.ZipFile(self.path("dup.zip"), "w") as zf:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # we create the dupe on purpose
                zf.writestr("a.xml", "1")
                zf.writestr("a.xml", "2")
        with self.assertRaises(PackageError):
            Workbook.open(self.path("dup.zip"))

    def test_path_traversal_entry(self):
        with zipfile.ZipFile(self.path("trav.zip"), "w") as zf:
            zf.writestr("../escape.xml", "x")
        with self.assertRaises(PackageError):
            Workbook.open(self.path("trav.zip"))

    def test_entity_declaration_forbidden(self):
        cache = ("<?xml version='1.0'?><!DOCTYPE cache [<!ENTITY x 'y'>]>"
                 "<cache id='c' source_sha256='x'><fields/><records/></cache>")
        self.assert_illegal([("c.xml", cache)], [("cr", "cache", "c.xml")])

    def test_missing_rels_part(self):
        with zipfile.ZipFile(self.path("nor.zip"), "w") as zf:
            zf.writestr("c.xml", "<cache/>")
        with self.assertRaises(PackageError):
            Workbook.open(self.path("nor.zip"))


# ----------------------------------------------------------------------
# multi-cache isolation
# ----------------------------------------------------------------------
class TestMultiCache(Base):
    def test_refresh_one_cache_leaves_the_other_untouched(self):
        c1 = cache_xml("c-one", "sha1", [("f", [member_xml("s", "A")]),
                                         ("g", [member_xml("n", "1")])], [(0, 0)])
        c2 = cache_xml("c-two", "sha2", [("h", [member_xml("s", "X")]),
                                         ("k", [member_xml("n", "2")])], [(0, 0)])
        p1 = pivot_xml("p-one", "c1r", 0, "", 1, "sum")
        p2 = pivot_xml("p-two", "c2r", 0, "", 1, "sum",
                       cells=[(key(("s", "X")), "[]", "2")])
        s1 = slicer_xml("s-one", "c1r", 0, "explicit",
                        items=[("s", "A", 0)], targets=["p1r"])
        parts = [("one/cache.xml", c1), ("two/cache.xml", c2),
                 ("one/p.xml", p1), ("two/p.xml", p2), ("one/s.xml", s1)]
        rels = [("c1r", "cache", "one/cache.xml"), ("c2r", "cache", "two/cache.xml"),
                ("p1r", "pivot", "one/p.xml"), ("p2r", "pivot", "two/p.xml"),
                ("s1r", "slicer", "one/s.xml")]
        extras = [("opaque/blob.bin", b"\x00\x01\x02")]
        wb_path = build_package(self.path("wb.zip"), parts, rels, extras)
        with zipfile.ZipFile(wb_path) as zf:
            before = {n: zf.read(n) for n in zf.namelist()}

        write_source(self.path("s.json"), ["g", "f"], [[5, "A"], [6, "B"]])
        wb = Workbook.open(wb_path)
        wb.refresh_cache("c-one", self.path("s.json"))

        with zipfile.ZipFile(wb_path) as zf:
            after = {n: zf.read(n) for n in zf.namelist()}
        for untouched in ("two/cache.xml", "two/p.xml", "opaque/blob.bin",
                          "_rels/package.xml"):
            self.assertEqual(before[untouched], after[untouched], untouched)
        self.assertNotEqual(before["one/cache.xml"], after["one/cache.xml"])

        # the other cache and its report are still fully functional
        self.assertEqual(wb.get_cache("c-two").read_records(),
                         [(("s", "X"), ("n", Decimal(2)))])
        self.assertEqual(wb.read_pivot("p-two").values(), {(key(("s", "X")), "[]"): "2"})
        self.assertEqual(wb.read_pivot("p-one").values(),
                         {(key(("s", "A")), "[]"): "5"})  # slicer still filters


# ----------------------------------------------------------------------
# independent cache reading
# ----------------------------------------------------------------------
class TestCacheIndependent(Base):
    def test_cache_part_roundtrip_without_workbook(self):
        xml = cache_xml("standalone", "deadbeef",
                        [("f", [member_xml("s", "A"), member_xml("m"),
                                member_xml("b", "1"), member_xml("n", "2.50")]),
                         ("g", [member_xml("n", "1")])],
                        [(0, 0), (1, 0), (2, 0), (3, 0)])
        cache = Cache.from_part(xml.encode("utf-8"))
        self.assertEqual(cache.id, "standalone")
        self.assertEqual(cache.source_sha256, "deadbeef")
        records = cache.read_records()
        self.assertEqual(records[1], (("m", None), ("n", Decimal(1))))
        self.assertEqual(records[2], (("b", True), ("n", Decimal(1))))
        self.assertEqual(records[3], (("n", Decimal("2.5")), ("n", Decimal(1))))
        # serialise -> parse again: identical model
        cache2 = Cache.from_part(cache.to_part())
        self.assertEqual(cache2.read_records(), records)
        self.assertEqual(cache2.field_names(), ["f", "g"])

    def test_cache_refresh_independent(self):
        cache = Cache.from_part(cache_xml("c", "x", [("f", [member_xml("s", "A")])],
                                          [(0,)]).encode())
        new = cache.refresh(["f"], [["B"], ["A"], [None]], "newsha")
        self.assertEqual(new.id, "c")
        self.assertEqual(new.source_sha256, "newsha")
        self.assertEqual(new.fields[0].members,
                         [("s", "B"), ("s", "A"), ("m", None)])  # first appearance
        self.assertEqual(new.read_records(),
                         [(("s", "B"),), (("s", "A"),), (("m", None),)])
        with self.assertRaises(SourceShapeError):
            cache.refresh(["other"], [["A"]])


# ----------------------------------------------------------------------
# persistence
# ----------------------------------------------------------------------
class TestPersistence(Base):
    def test_written_package_reopens_with_state(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache", os.path.join(MATERIALS, "source.json"))
        wb.set_selection("region", ["A", "C"])
        # a brand-new object reads exactly what was written
        wb2 = Workbook.open(wb_path)
        self.assertEqual(wb2.slicer_state("region").selected, [("s", "A"), ("s", "C")])
        with open(os.path.join(MATERIALS, "source.json"), "rb") as fh:
            expected_sha = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(wb2.get_cache("sales-cache").source_sha256, expected_sha)
        self.assertEqual(wb2.read_pivot("sales").values(),
                         {(key(("s", "A")), "[]"): "3", (key(("s", "C")), "[]"): "7"})

    def test_refresh_accepts_dict_and_bytes_sources(self):
        wb_path = self.make_sales_workbook()
        wb = Workbook.open(wb_path)
        wb.refresh_cache("sales-cache",
                         {"columns": ["amount", "region"], "rows": [[1, "A"]]})
        self.assertEqual(wb.read_pivot("sales").cells, [])  # C not selected by data
        raw = json.dumps({"columns": ["amount", "region"],
                          "rows": [[2, "C"]]}).encode("utf-8")
        wb.refresh_cache("sales-cache", raw)
        self.assertEqual(wb.get_cache("sales-cache").source_sha256,
                         hashlib.sha256(raw).hexdigest())
        self.assertEqual(wb.read_pivot("sales").values(), {(key(("s", "C")), "[]"): "2"})


if __name__ == "__main__":
    unittest.main()
