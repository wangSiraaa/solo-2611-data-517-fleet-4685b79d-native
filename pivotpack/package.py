"""ZIP container and package relationships.

The :class:`Package` keeps the entry list (order preserved) and the
parsed ``_rels/package.xml`` relationship table.  Parts the model does
not understand are never parsed and are copied back byte-for-byte.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from .errors import PackageError
from .xmlutil import parse_part, serialize_part, children

RELS_PART = "_rels/package.xml"
REL_KINDS = ("cache", "pivot", "slicer")


def _check_entry_name(name: str) -> None:
    if not name or name.startswith("/") or "\\" in name:
        raise PackageError(f"illegal ZIP entry name: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise PackageError(f"illegal ZIP entry name (path traversal): {name!r}")


class Rel:
    __slots__ = ("id", "kind", "target")

    def __init__(self, rel_id: str, kind: str, target: str):
        self.id = rel_id
        self.kind = kind
        self.target = target

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Rel({self.id!r}, {self.kind!r}, {self.target!r})"


class Package:
    """Ordered ZIP entries plus the relationship table."""

    def __init__(self):
        self.names = []                 # entry names in original order
        self.parts = {}                 # name -> bytes
        self.rels = []                  # list[Rel]
        self.rels_raw = b""             # original bytes of the rels part

    # ------------------------------------------------------------------
    @classmethod
    def read(cls, path: str) -> "Package":
        pkg = cls()
        try:
            zf = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise PackageError(f"not a ZIP package: {exc}") from exc
        with zf:
            seen = set()
            for info in zf.infolist():
                name = info.filename
                if name in seen:
                    raise PackageError(f"duplicate ZIP entry: {name!r}")
                seen.add(name)
                _check_entry_name(name)
                pkg.names.append(name)
                pkg.parts[name] = zf.read(name)
        if RELS_PART not in pkg.parts:
            raise PackageError(f"missing relationships part {RELS_PART!r}")
        pkg.rels_raw = pkg.parts[RELS_PART]
        pkg._parse_rels(pkg.parts[RELS_PART])
        return pkg

    def _parse_rels(self, data: bytes) -> None:
        root = parse_part(data, where=RELS_PART)
        if root.tag != "relationships":
            raise PackageError(f"{RELS_PART}: root must be 'relationships'")
        ids = set()
        for rel_el in children(root, "rel"):
            rel_id = rel_el.get("id")
            kind = rel_el.get("kind")
            target = rel_el.get("target")
            if not rel_id or not kind or not target:
                raise PackageError(f"{RELS_PART}: rel requires id, kind and target")
            if rel_id in ids:
                raise PackageError(f"{RELS_PART}: duplicate relationship id {rel_id!r}")
            ids.add(rel_id)
            if kind not in REL_KINDS:
                raise PackageError(f"{RELS_PART}: unknown relationship kind {kind!r}")
            _check_entry_name(target)
            if target not in self.parts:
                raise PackageError(
                    f"{RELS_PART}: relationship {rel_id!r} targets missing part {target!r}"
                )
            self.rels.append(Rel(rel_id, kind, target))

    # ------------------------------------------------------------------
    def rel(self, rel_id: str) -> Rel:
        for r in self.rels:
            if r.id == rel_id:
                return r
        raise PackageError(f"unknown relationship id {rel_id!r}")

    def rels_of_kind(self, kind: str):
        return [r for r in self.rels if r.kind == kind]

    def get(self, name: str) -> bytes:
        return self.parts[name]

    def replace(self, name: str, data: bytes) -> None:
        """Replace one part's bytes, keeping entry order and identity."""
        if name not in self.parts:
            raise PackageError(f"cannot replace unknown part {name!r}")
        self.parts[name] = data

    def serialize_rels(self) -> bytes:
        """Re-serialise the relationship table (only needed if rels change)."""
        root = ET.Element("relationships")
        for r in self.rels:
            ET.SubElement(root, "rel", {"id": r.id, "kind": r.kind, "target": r.target})
        return serialize_part(root)

    # ------------------------------------------------------------------
    def write_atomic(self, path: str) -> None:
        """Write the whole package to ``path`` atomically.

        The new ZIP is fully written and fsynced next to the target and
        then ``os.replace``-swapped in; a failure anywhere before the
        swap leaves the old file untouched.
        """
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".pivotpack-", suffix=".zip", dir=directory)
        try:
            with os.fdopen(fd, "wb") as fh:
                with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf:
                    for name in self.names:
                        # The rels part is rewritten from its original
                        # bytes: relationship identity is preserved
                        # bit-for-bit when nothing changed.
                        data = self.rels_raw if name == RELS_PART else self.parts[name]
                        zf.writestr(name, data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
