"""XML helpers shared by the part models.

Parsing forbids DOCTYPE/ENTITY declarations outright (no external
entities, no internal-subset expansion).  Serialisation is compact
single-line XML with a declaration, matching the style of the sample
package.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .errors import PackageError

XML_DECL = "<?xml version='1.0' encoding='utf-8'?>\n"

_FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY", b"<!doctype", b"<!entity")


def parse_part(data: bytes, *, where: str = "part") -> ET.Element:
    """Parse one XML part, rejecting entity/DOCTYPE declarations."""
    for token in _FORBIDDEN:
        if token in data:
            raise PackageError(f"{where}: DOCTYPE/ENTITY declarations are forbidden")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PackageError(f"{where}: malformed XML: {exc}") from exc


def serialize_part(root: ET.Element) -> bytes:
    return (XML_DECL + ET.tostring(root, encoding="unicode")).encode("utf-8")


def child(elem: ET.Element, tag: str) -> ET.Element | None:
    """First direct child with the given tag, or None."""
    for c in elem:
        if c.tag == tag:
            return c
    return None


def children(elem: ET.Element, tag: str):
    """All direct children with the given tag, in document order."""
    return [c for c in elem if c.tag == tag]
