"""Fixture builders for pivotpack tests.

These builders emit hand-written XML (not the library's serializers) so
that the tests exercise the reader against independently constructed
packages, including deliberately illegal ones.
"""

import json
import zipfile
from xml.sax.saxutils import quoteattr

XML_DECL = "<?xml version='1.0' encoding='utf-8'?>\n"


def _attrs(d):
    return "".join(f" {k}={quoteattr(str(v))}" for k, v in d.items() if v is not None)


def member_xml(kind, v=None):
    """One sharedItems member, e.g. member_xml('s', 'C') -> <s v="C" />."""
    if kind == "m":
        return "<m />"
    return f"<{kind}{_attrs({'v': v})} />"


def cache_xml(cache_id, sha, fields, records):
    """fields: [(name, [member_xml, ...])]; records: [(idx, ...), ...]."""
    out = [f"<cache{_attrs({'id': cache_id, 'source_sha256': sha})}><fields>"]
    for name, members in fields:
        out.append(f"<field{_attrs({'name': name})}><sharedItems>")
        out.extend(members)
        out.append("</sharedItems></field>")
    out.append("</fields><records>")
    for rec in records:
        out.append(f"<r{_attrs({'i': ' '.join(map(str, rec))})} />")
    out.append("</records></cache>")
    return XML_DECL + "".join(out)


def pivot_xml(pid, cache_rel, row, column, measure, aggregate,
              filters=(), cells=()):
    """filters: [(field_pos, [indices])]; cells: [(row_key, column_key, value)]."""
    head = _attrs({"id": pid, "cache_rel": cache_rel, "row": row,
                   "column": column, "measure": measure, "aggregate": aggregate})
    out = [f"<pivot{head}>", "<filters>"]
    for fpos, selected in filters:
        sel = " ".join(map(str, selected))
        out.append(f"<filter{_attrs({'field': fpos, 'selected': sel})} />")
    out.append("</filters><results>")
    for rk, ck, value in cells:
        out.append(f"<cell{_attrs({'row_key': rk, 'column_key': ck, 'value': value})} />")
    out.append("</results></pivot>")
    return XML_DECL + "".join(out)


def slicer_xml(sid, cache_rel, field, mode, items=(), targets=()):
    """items: [(kind, v, missing)]; targets: [pivot rel ids]."""
    head = _attrs({"id": sid, "cache_rel": cache_rel, "field": field, "mode": mode})
    out = [f"<slicer{head}>", "<selection>"]
    for kind, v, missing in items:
        out.append(f"<item{_attrs({'kind': kind, 'v': v, 'missing': missing})} />")
    out.append("</selection><targets>")
    for rel in targets:
        out.append(f"<pivot{_attrs({'rel': rel})} />")
    out.append("</targets></slicer>")
    return XML_DECL + "".join(out)


def rels_xml(rels):
    """rels: [(id, kind, target)]."""
    out = ["<relationships>"]
    for rid, kind, target in rels:
        out.append(f"<rel{_attrs({'id': rid, 'kind': kind, 'target': target})} />")
    out.append("</relationships>")
    return XML_DECL + "".join(out)


def build_package(path, parts, rels, extras=()):
    """Write a package ZIP.

    parts: [(target_name, cache/pivot/slicer xml)] in the order the rels
        reference them; rels: [(id, kind, target)]; extras: additional
        opaque entries [(name, bytes)].
    """
    entries = [("_rels/package.xml", rels_xml(rels))]
    entries.extend(parts)
    entries.extend(extras)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return path


def write_source(path, columns, rows):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"columns": columns, "rows": rows}, fh, ensure_ascii=False)
    return path


def write_source_raw(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
