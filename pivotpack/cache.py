"""The pivot cache: typed member dictionaries plus record index rows.

A :class:`Cache` is a self-contained model of one ``cache`` part.  It
can be parsed and read without any workbook context (other data tools
may depend on it), and parsing is *strict*: duplicate typed identities
inside a field, out-of-range record indices, wrong record arity and
malformed members all raise :class:`PackageError` instead of being
silently repaired.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .errors import PackageError, SourceShapeError
from .typed import (
    KINDS,
    member_from_kind_v,
    member_from_value,
    member_kind_v,
)
from .xmlutil import parse_part, serialize_part, child, children


class Field:
    """One cached field: a name plus its ordered member dictionary."""

    __slots__ = ("name", "members", "_index")

    def __init__(self, name: str, members):
        self.name = name
        self.members = list(members)
        self._index = {}
        for pos, m in enumerate(self.members):
            if m in self._index:
                kind, v = member_kind_v(m)
                raise PackageError(
                    f"field {name!r}: duplicate member identity {kind}:{v!r}"
                )
            self._index[m] = pos

    def position(self, member) -> int:
        return self._index[member]

    def __contains__(self, member) -> bool:
        return member in self._index

    def __len__(self) -> int:
        return len(self.members)


class Cache:
    """In-memory model of one cache part.

    Attributes:
        id:             the cache_id (preserved across refreshes).
        fields:         list of :class:`Field` in cache column order.
        records:        list of tuples of member indices (one tuple per
                        source row, aligned with ``fields``).
        source_sha256:  provenance digest of the source file bytes.
    """

    def __init__(self, cache_id: str, fields, records, source_sha256: str = ""):
        if not cache_id:
            raise PackageError("cache id must be non-empty")
        self.id = cache_id
        self.fields = list(fields)
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names) or any(not n for n in names):
            raise PackageError(f"cache {cache_id!r}: field names must be unique and non-empty")
        self.records = [tuple(r) for r in records]
        self.source_sha256 = source_sha256
        self._check_records()

    def _check_records(self) -> None:
        nfields = len(self.fields)
        for rno, rec in enumerate(self.records):
            if len(rec) != nfields:
                raise PackageError(
                    f"cache {self.id!r} record {rno}: expected {nfields} "
                    f"member indices, got {len(rec)}"
                )
            for fpos, idx in enumerate(rec):
                if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
                    raise PackageError(
                        f"cache {self.id!r} record {rno} field {fpos}: "
                        f"invalid member index {idx!r}"
                    )
                if idx >= len(self.fields[fpos]):
                    raise PackageError(
                        f"cache {self.id!r} record {rno} field {fpos}: "
                        f"member index {idx} out of range "
                        f"({len(self.fields[fpos])} members)"
                    )

    # ------------------------------------------------------------------
    # construction / serialisation
    # ------------------------------------------------------------------
    @classmethod
    def from_part(cls, data: bytes) -> "Cache":
        """Parse and fully validate a cache part."""
        root = parse_part(data, where="cache part")
        if root.tag != "cache":
            raise PackageError(f"cache part: root element must be 'cache', got {root.tag!r}")
        cache_id = root.get("id")
        if not cache_id:
            raise PackageError("cache part: missing 'id' attribute")
        source_sha256 = root.get("source_sha256")
        if source_sha256 is None:
            raise PackageError(f"cache {cache_id!r}: missing 'source_sha256' attribute")

        fields_el = child(root, "fields")
        records_el = child(root, "records")
        if fields_el is None or records_el is None:
            raise PackageError(f"cache {cache_id!r}: requires 'fields' and 'records' elements")

        fields = []
        for fel in children(fields_el, "field"):
            name = fel.get("name")
            if not name:
                raise PackageError(f"cache {cache_id!r}: field without a name")
            shared = child(fel, "sharedItems")
            members = []
            if shared is not None:
                for item in shared:
                    if item.tag not in KINDS:
                        raise PackageError(
                            f"cache {cache_id!r} field {name!r}: "
                            f"unknown sharedItems element {item.tag!r}"
                        )
                    members.append(
                        member_from_kind_v(
                            item.tag, item.get("v"),
                            where=f"cache {cache_id!r} field {name!r}",
                        )
                    )
            fields.append(Field(name, members))  # duplicate identity -> PackageError

        records = []
        for rel in children(records_el, "r"):
            raw = rel.get("i")
            if raw is None:
                raise PackageError(f"cache {cache_id!r}: record without 'i' attribute")
            indices = []
            for token in raw.split():
                if not token.isdigit():
                    raise PackageError(
                        f"cache {cache_id!r}: bad member index {token!r}"
                    )
                indices.append(int(token))
            records.append(tuple(indices))
        return cls(cache_id, fields, records, source_sha256)

    def to_part(self) -> bytes:
        root = ET.Element("cache", {"id": self.id, "source_sha256": self.source_sha256})
        fields_el = ET.SubElement(root, "fields")
        for field in self.fields:
            fel = ET.SubElement(fields_el, "field", {"name": field.name})
            shared = ET.SubElement(fel, "sharedItems")
            for member in field.members:
                kind, v = member_kind_v(member)
                attrs = {} if v is None else {"v": v}
                ET.SubElement(shared, kind, attrs)
        records_el = ET.SubElement(root, "records")
        for rec in self.records:
            ET.SubElement(records_el, "r", {"i": " ".join(str(i) for i in rec)})
        return serialize_part(root)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def field_names(self):
        return [f.name for f in self.fields]

    def field_position(self, name: str) -> int:
        for pos, f in enumerate(self.fields):
            if f.name == name:
                return pos
        raise PackageError(f"cache {self.id!r}: no field named {name!r}")

    def member(self, field_pos: int, index: int):
        return self.fields[field_pos].members[index]

    def read_records(self):
        """Return all records as tuples of typed members.

        Integrity (arity, index range, dictionary uniqueness) was
        verified at construction, so this never silently patches gaps.
        """
        out = []
        for rec in self.records:
            out.append(tuple(self.fields[p].members[i] for p, i in enumerate(rec)))
        return out

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------
    def refresh(self, columns, rows, source_sha256: str = "") -> "Cache":
        """Build the successor cache from new source data.

        ``columns`` must be exactly the existing field names, in any
        order; the new cache adopts the source column order.  Members
        are collected per field in first-appearance order.  The cache_id
        is preserved; dictionaries and record indices are rebuilt from
        scratch (never patched in place).
        """
        columns = list(columns)
        checked_rows = []
        for rno, r in enumerate(rows):
            if not isinstance(r, (list, tuple)):
                raise SourceShapeError(f"source row {rno}: expected an array, got {r!r}")
            checked_rows.append(list(r))
        rows = checked_rows
        if len(set(columns)) != len(columns) or any(not isinstance(c, str) or not c for c in columns):
            raise SourceShapeError("source columns must be unique non-empty names")
        old_names = set(self.field_names())
        if set(columns) != old_names:
            raise SourceShapeError(
                f"source columns {sorted(columns)!r} do not match cache "
                f"fields {sorted(old_names)!r}"
            )
        nfields = len(columns)
        members = [[] for _ in columns]           # per-column ordered members
        index = [{} for _ in columns]             # member -> position
        records = []
        for rno, row in enumerate(rows):
            if len(row) != nfields:
                raise SourceShapeError(
                    f"source row {rno}: expected {nfields} values, got {len(row)}"
                )
            rec = []
            for cpos, value in enumerate(row):
                m = member_from_value(value, where=f"source row {rno} column {columns[cpos]!r}")
                pos = index[cpos].get(m)
                if pos is None:
                    pos = len(members[cpos])
                    index[cpos][m] = pos
                    members[cpos].append(m)
                rec.append(pos)
            records.append(tuple(rec))
        fields = [Field(name, members[i]) for i, name in enumerate(columns)]
        return Cache(self.id, fields, records, source_sha256)
