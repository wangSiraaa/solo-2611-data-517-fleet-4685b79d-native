"""Workbook: relationships, field-position migration, selections, reports.

The workbook layer owns everything the cache layer must not know about:
package relationships, pivot/slicer parts, the migration of field
*positions* across a refresh (positions are re-resolved through field
*names*), slicer selection state (typed identities with a missing
flag), pivot recomputation and the atomic one-shot save.

Nothing here copies new cache arrays over old indices: every binding
that used to be "position p in the old dictionary" is first resolved to
a durable identity (field name / typed member), then re-bound to the
new cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
import xml.etree.ElementTree as ET

from .cache import Cache
from .errors import (
    MeasureError,
    PackageError,
    PivotPackError,
    SourceShapeError,
    UnknownCacheError,
    UnknownPivotError,
    UnknownSlicerError,
)
from .package import Package
from .typed import (
    KINDS,
    axis_key_json,
    axis_key_parse,
    canon_decimal,
    coerce_member,
    exact_sum,
    member_from_kind_v,
    member_kind_v,
    parse_decimal,
)
from .xmlutil import parse_part, serialize_part, child, children

AGGREGATES = ("sum", "count")
SLICER_MODES = ("all", "explicit")


# ----------------------------------------------------------------------
# part models
# ----------------------------------------------------------------------
class Filter:
    """One pivot field filter: a field position plus selected member indices."""

    __slots__ = ("field", "selected")

    def __init__(self, field: int, selected):
        self.field = field
        self.selected = list(selected)


@dataclass
class Cell:
    row_key: tuple      # typed members, length 1
    column_key: tuple   # typed members, length 0 or 1
    value: str          # canonical decimal string


class Pivot:
    def __init__(self, rel_id, pivot_id, cache_rel, row, column, measure,
                 aggregate, filters, cells):
        self.rel_id = rel_id
        self.id = pivot_id
        self.cache_rel = cache_rel
        self.row = row
        self.column = column          # int or None
        self.measure = measure
        self.aggregate = aggregate
        self.filters = filters        # list[Filter]
        self.cells = cells            # list[Cell]

    # ------------------------------------------------------------------
    @classmethod
    def from_part(cls, data: bytes, rel_id: str, caches: dict) -> "Pivot":
        where = f"pivot part (rel {rel_id!r})"
        root = parse_part(data, where=where)
        if root.tag != "pivot":
            raise PackageError(f"{where}: root element must be 'pivot'")
        pivot_id = root.get("id")
        cache_rel = root.get("cache_rel")
        aggregate = root.get("aggregate")
        if not pivot_id or not cache_rel:
            raise PackageError(f"{where}: missing 'id' or 'cache_rel'")
        if aggregate not in AGGREGATES:
            raise PackageError(f"{where}: aggregate must be one of {AGGREGATES}")
        cache = caches.get(cache_rel)
        if cache is None:
            raise PackageError(
                f"{where}: references unknown cache relationship {cache_rel!r}"
            )
        nfields = len(cache.fields)

        def pos_attr(name, allow_empty=False):
            raw = root.get(name)
            if raw is None:
                raise PackageError(f"{where}: missing attribute {name!r}")
            if raw == "" and allow_empty:
                return None
            if not raw.isdigit():
                raise PackageError(f"{where}: attribute {name!r} must be a field position, got {raw!r}")
            pos = int(raw)
            if pos >= nfields:
                raise PackageError(
                    f"{where}: {name} position {pos} out of range ({nfields} fields)"
                )
            return pos

        row = pos_attr("row")
        column = pos_attr("column", allow_empty=True)
        measure = pos_attr("measure")

        filters = []
        filters_el = child(root, "filters")
        if filters_el is not None:
            for fel in children(filters_el, "filter"):
                fraw = fel.get("field")
                if fraw is None or not fraw.isdigit() or int(fraw) >= nfields:
                    raise PackageError(f"{where}: filter with bad field position {fraw!r}")
                fpos = int(fraw)
                selected = []
                for token in (fel.get("selected") or "").split():
                    if not token.isdigit():
                        raise PackageError(f"{where}: bad filter member index {token!r}")
                    idx = int(token)
                    if idx >= len(cache.fields[fpos]):
                        raise PackageError(
                            f"{where}: filter member index {idx} out of range for "
                            f"field {cache.fields[fpos].name!r}"
                        )
                    selected.append(idx)
                filters.append(Filter(fpos, selected))

        cells = []
        results_el = child(root, "results")
        if results_el is not None:
            for cel in children(results_el, "cell"):
                rk = cel.get("row_key")
                ck = cel.get("column_key")
                val = cel.get("value")
                if rk is None or ck is None or val is None:
                    raise PackageError(f"{where}: cell requires row_key, column_key and value")
                row_key = axis_key_parse(rk, where=f"{where} row_key")
                column_key = axis_key_parse(ck, where=f"{where} column_key")
                if len(row_key) != 1:
                    raise PackageError(f"{where}: row_key must hold exactly one member")
                if len(column_key) != (1 if column is not None else 0):
                    raise PackageError(f"{where}: column_key arity does not match the pivot")
                value = canon_decimal(parse_decimal(val, where=f"{where} cell value"))
                cells.append(Cell(row_key, column_key, value))
        return cls(rel_id, pivot_id, cache_rel, row, column, measure,
                   aggregate, filters, cells)

    # ------------------------------------------------------------------
    def to_part(self) -> bytes:
        root = ET.Element("pivot", {
            "id": self.id,
            "cache_rel": self.cache_rel,
            "row": str(self.row),
            "column": "" if self.column is None else str(self.column),
            "measure": str(self.measure),
            "aggregate": self.aggregate,
        })
        filters_el = ET.SubElement(root, "filters")
        for f in self.filters:
            ET.SubElement(filters_el, "filter", {
                "field": str(f.field),
                "selected": " ".join(str(i) for i in f.selected),
            })
        results_el = ET.SubElement(root, "results")
        for c in self.cells:
            ET.SubElement(results_el, "cell", {
                "row_key": axis_key_json(c.row_key),
                "column_key": axis_key_json(c.column_key),
                "value": c.value,
            })
        return serialize_part(root)


class Slicer:
    def __init__(self, rel_id, slicer_id, cache_rel, field, mode, items, targets):
        self.rel_id = rel_id
        self.id = slicer_id
        self.cache_rel = cache_rel
        self.field = field
        self.mode = mode
        self.items = items            # list[(member, missing: bool)]
        self.targets = targets        # list of pivot relationship ids

    # ------------------------------------------------------------------
    @classmethod
    def from_part(cls, data: bytes, rel_id: str, caches: dict) -> "Slicer":
        where = f"slicer part (rel {rel_id!r})"
        root = parse_part(data, where=where)
        if root.tag != "slicer":
            raise PackageError(f"{where}: root element must be 'slicer'")
        slicer_id = root.get("id")
        cache_rel = root.get("cache_rel")
        mode = root.get("mode")
        if not slicer_id or not cache_rel:
            raise PackageError(f"{where}: missing 'id' or 'cache_rel'")
        if mode not in SLICER_MODES:
            raise PackageError(f"{where}: mode must be one of {SLICER_MODES}")
        cache = caches.get(cache_rel)
        if cache is None:
            raise PackageError(
                f"{where}: references unknown cache relationship {cache_rel!r}"
            )
        fraw = root.get("field")
        if fraw is None or not fraw.isdigit() or int(fraw) >= len(cache.fields):
            raise PackageError(f"{where}: bad field position {fraw!r}")

        items = []
        selection_el = child(root, "selection")
        if selection_el is not None:
            for iel in children(selection_el, "item"):
                kind = iel.get("kind")
                if kind not in KINDS:
                    raise PackageError(f"{where}: unknown item kind {kind!r}")
                member = member_from_kind_v(kind, iel.get("v"), where=where)
                missing_raw = iel.get("missing", "0")
                if missing_raw not in ("0", "1"):
                    raise PackageError(f"{where}: missing must be 0 or 1")
                items.append((member, missing_raw == "1"))

        targets = []
        targets_el = child(root, "targets")
        if targets_el is not None:
            for pel in children(targets_el, "pivot"):
                rel = pel.get("rel")
                if not rel:
                    raise PackageError(f"{where}: target pivot without 'rel'")
                targets.append(rel)
        return cls(rel_id, slicer_id, cache_rel, int(fraw), mode, items, targets)

    # ------------------------------------------------------------------
    def to_part(self) -> bytes:
        root = ET.Element("slicer", {
            "id": self.id,
            "cache_rel": self.cache_rel,
            "field": str(self.field),
            "mode": self.mode,
        })
        selection_el = ET.SubElement(root, "selection")
        if self.mode == "explicit":
            for member, missing in self.items:
                kind, v = member_kind_v(member)
                attrs = {"kind": kind}
                if v is not None:
                    attrs["v"] = v
                attrs["missing"] = "1" if missing else "0"
                ET.SubElement(selection_el, "item", attrs)
        targets_el = ET.SubElement(root, "targets")
        for rel in self.targets:
            ET.SubElement(targets_el, "pivot", {"rel": rel})
        return serialize_part(root)


# ----------------------------------------------------------------------
# public result views
# ----------------------------------------------------------------------
@dataclass
class PivotResult:
    """What :meth:`Workbook.read_pivot` returns."""

    id: str
    rel_id: str
    cache_id: str
    row_field: str
    column_field: str | None
    measure_field: str
    aggregate: str
    cells: list  # list[Cell]

    def values(self):
        """{(row_key_json, column_key_json): canonical decimal string}."""
        return {(axis_key_json(c.row_key), axis_key_json(c.column_key)): c.value
                for c in self.cells}


@dataclass
class SlicerState:
    """What :meth:`Workbook.slicer_state` returns."""

    id: str
    rel_id: str
    cache_id: str
    field_name: str
    mode: str
    items: list       # list[(member, missing: bool)]
    targets: list     # list of target pivot ids

    @property
    def selected(self):
        """Selected typed members (including currently-missing ones)."""
        return [m for (m, _missing) in self.items]


@dataclass
class RefreshReport:
    cache_id: str
    source_sha256: str
    fields: list
    member_counts: dict
    record_count: int
    pivots_recomputed: list
    slicers_updated: list
    missing_selections: dict   # slicer id -> list of missing members

    def summary(self) -> str:
        lines = [
            f"cache {self.cache_id!r} refreshed:",
            f"  source_sha256 = {self.source_sha256}",
            f"  fields        = {', '.join(self.fields)}",
            f"  members       = "
            + ", ".join(f"{k}:{v}" for k, v in self.member_counts.items()),
            f"  records       = {self.record_count}",
            f"  pivots        = {', '.join(self.pivots_recomputed) or '(none)'}",
            f"  slicers       = {', '.join(self.slicers_updated) or '(none)'}",
        ]
        for sid, missing in self.missing_selections.items():
            if missing:
                lines.append(f"  missing in {sid}: "
                             + ", ".join(f"{k}:{v}" for k, v in map(member_kind_v, missing)))
        return "\n".join(lines)


# ----------------------------------------------------------------------
# source loading
# ----------------------------------------------------------------------
def _reject_constant(token):
    raise SourceShapeError(f"source contains non-finite number literal {token!r}")


def _canon_json(obj) -> str:
    """Deterministic JSON text used only for provenance hashing."""
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, Decimal):
        return canon_decimal(obj)
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return canon_decimal(Decimal(str(obj)))
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canon_json(x) for x in obj) + "]"
    if isinstance(obj, dict):
        return "{" + ",".join(
            json.dumps(str(k), ensure_ascii=False) + ":" + _canon_json(v)
            for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))
        ) + "}"
    raise SourceShapeError(f"cannot hash source value of type {type(obj).__name__}")


def load_source(source):
    """Normalise a refresh source into (columns, rows, source_sha256).

    Accepts a filesystem path, raw JSON bytes, or an already-parsed
    ``{"columns": [...], "rows": [...]}`` mapping.
    """
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as fh:
            raw = fh.read()
        sha = hashlib.sha256(raw).hexdigest()
        try:
            data = json.loads(raw, parse_float=Decimal, parse_int=Decimal,
                              parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise SourceShapeError(f"source is not valid JSON: {exc}") from exc
    elif isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        sha = hashlib.sha256(raw).hexdigest()
        try:
            data = json.loads(raw, parse_float=Decimal, parse_int=Decimal,
                              parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise SourceShapeError(f"source is not valid JSON: {exc}") from exc
    elif isinstance(source, dict):
        data = source
        sha = hashlib.sha256(_canon_json(source).encode("utf-8")).hexdigest()
    else:
        raise SourceShapeError(f"unsupported source type {type(source).__name__}")

    if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
        raise SourceShapeError("source must be an object with 'columns' and 'rows'")
    columns = data["columns"]
    rows = data["rows"]
    if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns):
        raise SourceShapeError("source 'columns' must be an array of strings")
    if not isinstance(rows, list) or any(not isinstance(r, list) for r in rows):
        raise SourceShapeError("source 'rows' must be an array of arrays")
    return columns, rows, sha


# ----------------------------------------------------------------------
# pivot recomputation
# ----------------------------------------------------------------------
def compute_cells(pivot: Pivot, cache: Cache, slicers) -> list:
    """Recompute one pivot's cells from cache records.

    ``slicers`` are the (already migrated) slicers that target this
    pivot.  Filtering is by typed identity; missing slicer members
    simply match nothing.  A non-null non-numeric measure value in any
    consumed record raises :class:`MeasureError` (refresh additionally
    validates the whole measure column up front -- see
    :meth:`Workbook.refresh_cache`).
    """
    constraints = []  # (field position, set of allowed typed members)
    for s in slicers:
        if s.mode == "all":
            continue
        constraints.append((s.field, {m for (m, _missing) in s.items}))
    for f in pivot.filters:
        constraints.append((f.field, {cache.member(f.field, i) for i in f.selected}))

    groups = {}
    for rec in cache.read_records():
        if any(rec[fpos] not in allowed for fpos, allowed in constraints):
            continue
        mv = rec[pivot.measure]
        if mv[0] == "m":
            number = None
        elif mv[0] == "n":
            number = mv[1]
        else:
            kind, v = member_kind_v(mv)
            raise MeasureError(
                f"pivot {pivot.id!r}: measure field "
                f"{cache.fields[pivot.measure].name!r} holds non-numeric "
                f"member {kind}:{v!r}"
            )
        row_key = (rec[pivot.row],)
        column_key = () if pivot.column is None else (rec[pivot.column],)
        groups.setdefault((row_key, column_key), []).append(number)

    cells = []
    for (row_key, column_key), numbers in groups.items():
        if pivot.aggregate == "sum":
            value = canon_decimal(exact_sum(n for n in numbers if n is not None))
        else:  # count
            value = str(sum(1 for n in numbers if n is not None))
        cells.append(Cell(row_key, column_key, value))
    cells.sort(key=lambda c: (axis_key_json(c.row_key), axis_key_json(c.column_key)))
    return cells


# ----------------------------------------------------------------------
# the workbook
# ----------------------------------------------------------------------
class Workbook:
    """A restricted workbook package opened from a ZIP file."""

    def __init__(self, path):
        self._path = str(path)
        self._load()

    @classmethod
    def open(cls, path) -> "Workbook":
        return cls(path)

    # ------------------------------------------------------------------
    # loading / validation
    # ------------------------------------------------------------------
    def _load(self) -> None:
        pkg = Package.read(self._path)

        caches = {}       # cache rel id -> Cache
        for rel in pkg.rels_of_kind("cache"):
            cache = Cache.from_part(pkg.get(rel.target))
            if any(c.id == cache.id for c in caches.values()):
                raise PackageError(f"duplicate cache id {cache.id!r}")
            caches[rel.id] = cache

        pivots = {}       # pivot rel id -> Pivot
        for rel in pkg.rels_of_kind("pivot"):
            pivot = Pivot.from_part(pkg.get(rel.target), rel.id, caches)
            if any(p.id == pivot.id for p in pivots.values()):
                raise PackageError(f"duplicate pivot id {pivot.id!r}")
            pivots[rel.id] = pivot

        slicers = {}      # slicer rel id -> Slicer
        for rel in pkg.rels_of_kind("slicer"):
            slicer = Slicer.from_part(pkg.get(rel.target), rel.id, caches)
            if any(s.id == slicer.id for s in slicers.values()):
                raise PackageError(f"duplicate slicer id {slicer.id!r}")
            for target in slicer.targets:
                if target not in pivots:
                    raise PackageError(
                        f"slicer {slicer.id!r} targets unknown pivot relationship {target!r}"
                    )
                if pivots[target].cache_rel != slicer.cache_rel:
                    raise PackageError(
                        f"slicer {slicer.id!r} targets pivot {pivots[target].id!r} "
                        f"of a different cache"
                    )
            slicers[rel.id] = slicer

        self._package = pkg
        self._caches = caches      # cache rel id -> Cache
        self._pivots = pivots      # pivot rel id -> Pivot
        self._slicers = slicers    # slicer rel id -> Slicer
        self._cache_rel_by_id = {c.id: rid for rid, c in caches.items()}

    # ------------------------------------------------------------------
    # listings
    # ------------------------------------------------------------------
    def cache_ids(self):
        return [c.id for c in self._caches.values()]

    def pivot_ids(self):
        return [p.id for p in self._pivots.values()]

    def slicer_ids(self):
        return [s.id for s in self._slicers.values()]

    # ------------------------------------------------------------------
    # lookups
    # ------------------------------------------------------------------
    def get_cache(self, cache_id: str) -> Cache:
        rel_id = self._cache_rel_by_id.get(cache_id)
        if rel_id is None:
            raise UnknownCacheError(f"no cache with id {cache_id!r}")
        return self._caches[rel_id]

    def _find_pivot_rel(self, pivot_id: str) -> str:
        for rid, p in self._pivots.items():
            if p.id == pivot_id or rid == pivot_id:
                return rid
        raise UnknownPivotError(f"no pivot with id {pivot_id!r}")

    def _find_slicer_rel(self, slicer_id: str) -> str:
        for rid, s in self._slicers.items():
            if s.id == slicer_id or rid == slicer_id:
                return rid
        raise UnknownSlicerError(f"no slicer with id {slicer_id!r}")

    def _slicers_targeting(self, pivot_rel: str, cache_rel: str):
        return [s for s in self._slicers.values()
                if s.cache_rel == cache_rel and pivot_rel in s.targets]

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def read_pivot(self, pivot_id: str) -> PivotResult:
        """Return the pivot's stored output: axis identities and values."""
        rid = self._find_pivot_rel(pivot_id)
        p = self._pivots[rid]
        cache = self._caches[p.cache_rel]
        return PivotResult(
            id=p.id,
            rel_id=rid,
            cache_id=cache.id,
            row_field=cache.fields[p.row].name,
            column_field=None if p.column is None else cache.fields[p.column].name,
            measure_field=cache.fields[p.measure].name,
            aggregate=p.aggregate,
            cells=list(p.cells),
        )

    def slicer_state(self, slicer_id: str) -> SlicerState:
        rid = self._find_slicer_rel(slicer_id)
        s = self._slicers[rid]
        cache = self._caches[s.cache_rel]
        return SlicerState(
            id=s.id,
            rel_id=rid,
            cache_id=cache.id,
            field_name=cache.fields[s.field].name,
            mode=s.mode,
            items=list(s.items),
            targets=[self._pivots[t].id for t in s.targets],
        )

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------
    def refresh_cache(self, cache_id: str, source) -> RefreshReport:
        """Refresh one cache from new source data and rebind everything.

        The whole operation is atomic: the successor cache is built,
        every dependent slicer and pivot is migrated (field positions
        and filter selections re-resolved through names and typed
        identities) and all pivots are recomputed *before* anything is
        written; the new ZIP is then swapped in with ``os.replace``.
        Any source-shape or measure error leaves the old package --
        and therefore every report -- untouched and readable.
        """
        rel_id = self._cache_rel_by_id.get(cache_id)
        if rel_id is None:
            raise UnknownCacheError(f"no cache with id {cache_id!r}")
        columns, rows, sha = load_source(source)

        old_cache = self._caches[rel_id]
        new_cache = old_cache.refresh(columns, rows, sha)  # validates shape

        def migrate_pos(pos):
            return new_cache.field_position(old_cache.fields[pos].name)

        # --- migrate slicers of this cache (field position + missing flags)
        new_slicers = {}
        for srid, s in self._slicers.items():
            if s.cache_rel != rel_id:
                continue
            new_field = migrate_pos(s.field)
            dictionary = new_cache.fields[new_field]
            items = [(m, m not in dictionary) for (m, _old) in s.items]
            new_slicers[srid] = Slicer(srid, s.id, s.cache_rel, new_field,
                                       s.mode, items, list(s.targets))

        # --- migrate pivots of this cache (positions + filter selections)
        new_pivots = {}
        for prid, p in self._pivots.items():
            if p.cache_rel != rel_id:
                continue
            filters = []
            for f in p.filters:
                new_fpos = migrate_pos(f.field)
                dictionary = new_cache.fields[new_fpos]
                selected = []
                for idx in f.selected:
                    member = old_cache.member(f.field, idx)
                    if member in dictionary:
                        selected.append(dictionary.position(member))
                filters.append(Filter(new_fpos, selected))
            new_pivots[prid] = Pivot(
                prid, p.id, p.cache_rel,
                migrate_pos(p.row),
                None if p.column is None else migrate_pos(p.column),
                migrate_pos(p.measure),
                p.aggregate, filters, [],
            )

        # --- measure contract: each pivot's measure column must be
        # numeric-or-null across the whole refreshed cache.  This is a
        # column-level data contract, deliberately independent of the
        # current slicer/filter state, and it is enforced before
        # anything is written so a violation blocks the entire refresh.
        records = new_cache.read_records()
        for p in new_pivots.values():
            measure_name = new_cache.fields[p.measure].name
            for rec in records:
                mv = rec[p.measure]
                if mv[0] not in ("m", "n"):
                    kind, v = member_kind_v(mv)
                    raise MeasureError(
                        f"pivot {p.id!r}: measure field {measure_name!r} "
                        f"holds non-numeric member {kind}:{v!r}"
                    )

        # --- recompute every migrated pivot against the same new records
        for prid, p in new_pivots.items():
            slicers = [ns for ns in new_slicers.values() if prid in ns.targets]
            p.cells = compute_cells(p, new_cache, slicers)

        # --- everything computed; rewrite only this cache's parts.
        # Work on a fresh copy of the on-disk package so a failure here
        # cannot leave the in-memory model inconsistent with the file.
        pkg = Package.read(self._path)
        pkg.replace(pkg.rel(rel_id).target, new_cache.to_part())
        for srid, s in new_slicers.items():
            pkg.replace(pkg.rel(srid).target, s.to_part())
        for prid, p in new_pivots.items():
            pkg.replace(pkg.rel(prid).target, p.to_part())
        pkg.write_atomic(self._path)
        self._load()  # re-parse exactly what a new process will read

        return RefreshReport(
            cache_id=cache_id,
            source_sha256=sha,
            fields=new_cache.field_names(),
            member_counts={f.name: len(f) for f in new_cache.fields},
            record_count=len(new_cache.records),
            pivots_recomputed=[p.id for p in new_pivots.values()],
            slicers_updated=[s.id for s in new_slicers.values()],
            missing_selections={
                s.id: [m for (m, miss) in s.items if miss]
                for s in new_slicers.values()
            },
        )

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def set_selection(self, slicer_id: str, selection=None, mode=None) -> None:
        """Replace a slicer's selection and recompute its target pivots.

        ``selection`` is an iterable of typed members (raw scalars,
        ``(kind, value)`` tuples or ``{"kind","v"}`` dicts).  Members
        absent from the current dictionary are kept with ``missing=1``;
        an empty iterable is an explicit empty selection (zero rows),
        which is different from ``mode="all"`` (no filtering at all).
        """
        srid = self._find_slicer_rel(slicer_id)
        s = self._slicers[srid]
        cache = self._caches[s.cache_rel]

        if mode is None:
            mode = "explicit" if selection is not None else s.mode
        if mode not in SLICER_MODES:
            raise PivotPackError(f"mode must be one of {SLICER_MODES}")

        if mode == "all":
            items = []
        else:
            raw = selection if selection is not None else [m for (m, _) in s.items]
            dictionary = cache.fields[s.field]
            items = []
            seen = set()
            for obj in raw:
                member = coerce_member(obj)
                if member not in seen:
                    seen.add(member)
                    items.append((member, member not in dictionary))

        new_slicer = Slicer(srid, s.id, s.cache_rel, s.field, mode, items,
                            list(s.targets))

        # recompute only the pivots this slicer drives
        affected = {}
        for prid in s.targets:
            p = self._pivots[prid]
            slicers = [new_slicer if x.rel_id == srid else x
                       for x in self._slicers_targeting(prid, s.cache_rel)]
            updated = Pivot(prid, p.id, p.cache_rel, p.row, p.column, p.measure,
                            p.aggregate, p.filters,
                            compute_cells(p, cache, slicers))
            affected[prid] = updated

        pkg = Package.read(self._path)
        pkg.replace(pkg.rel(srid).target, new_slicer.to_part())
        for prid, p in affected.items():
            pkg.replace(pkg.rel(prid).target, p.to_part())
        pkg.write_atomic(self._path)
        self._load()
