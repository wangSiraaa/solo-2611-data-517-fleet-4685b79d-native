"""Typed member identities.

A *member* is the typed identity of one cell value.  Everything in the
system -- cache dictionaries, record values, slicer selections, pivot
axis keys -- is keyed by this identity, never by a dictionary position
or a display string.

Representation: a 2-tuple ``(kind, value)`` where ``kind`` is one of

* ``"m"`` -- missing / null.  ``value`` is ``None``.
* ``"b"`` -- boolean.  ``value`` is a Python ``bool``.
* ``"n"`` -- finite decimal number.  ``value`` is a
  :class:`decimal.Decimal`.  ``1`` and ``1.0`` (and ``-0``) collapse to
  the same identity because ``Decimal`` equality/hash are numeric.
* ``"s"`` -- Unicode text.  ``value`` is a ``str``.

``("b", True)``, ``("n", Decimal(1))`` and ``("s", "1")`` are three
different identities, as required.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation, localcontext

from .errors import PackageError, SourceShapeError

KINDS = ("m", "b", "n", "s")

MISSING = ("m", None)


def canon_decimal(d: Decimal) -> str:
    """Canonical string for a finite Decimal (plain notation, no -0).

    Uses ``format(d, 'f')`` plus trailing-zero stripping instead of
    ``Decimal.normalize()`` so values with more significant digits than
    the default decimal context (28) are never rounded.
    """
    if not d.is_finite():
        raise ValueError(f"non-finite decimal: {d!r}")
    if d == 0:
        return "0"
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def exact_sum(numbers) -> Decimal:
    """Sum Decimals exactly, sizing the context to the operands.

    Plain ``+`` rounds every partial sum to the ambient context
    precision; here the precision is derived from the operand widths so
    the result is exact for any finite decimal input.
    """
    nums = list(numbers)
    if not nums:
        return Decimal(0)
    int_digits = max(max(d.adjusted() + 1, 0) for d in nums)
    frac_digits = max(max(-d.as_tuple().exponent, 0) for d in nums)
    guard = len(nums).bit_length() + 2
    with localcontext() as ctx:
        ctx.prec = max(1, int_digits + frac_digits + guard)
        total = Decimal(0)
        for d in nums:
            total += d
    return total


def parse_decimal(text: str, *, where: str = "number") -> Decimal:
    """Parse a finite decimal string, rejecting NaN/Infinity."""
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        raise PackageError(f"{where}: not a decimal: {text!r}") from None
    if not d.is_finite():
        raise PackageError(f"{where}: non-finite decimal: {text!r}")
    return d


def member_from_value(v, *, where: str = "value"):
    """Convert a JSON/Python scalar to a typed member.

    Accepts None, bool, int, float, Decimal, str.  Anything else is a
    source-shape violation.
    """
    if v is None:
        return ("m", None)
    if isinstance(v, bool):  # before int: bool is a subclass of int
        return ("b", v)
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise SourceShapeError(f"{where}: non-finite number {v!r}")
        return ("n", v)
    if isinstance(v, int):
        return ("n", Decimal(v))
    if isinstance(v, float):
        if not math.isfinite(v):
            raise SourceShapeError(f"{where}: non-finite number {v!r}")
        return ("n", Decimal(str(v)))
    if isinstance(v, str):
        return ("s", v)
    raise SourceShapeError(
        f"{where}: unsupported value type {type(v).__name__}: {v!r}"
    )


def member_kind_v(member) -> tuple[str, str | None]:
    """(kind, canonical v-string or None) used for XML and JSON codecs."""
    kind, value = member
    if kind == "m":
        return "m", None
    if kind == "b":
        return "b", "1" if value else "0"
    if kind == "n":
        return "n", canon_decimal(value)
    if kind == "s":
        return "s", value
    raise PackageError(f"unknown member kind: {kind!r}")


def member_from_kind_v(kind: str, v: str | None, *, where: str = "member"):
    """Build a typed member from a kind tag and a raw string v."""
    if kind == "m":
        if v is not None:
            raise PackageError(f"{where}: 'm' member must not carry a value")
        return ("m", None)
    if v is None:
        raise PackageError(f"{where}: {kind!r} member requires a value")
    if kind == "b":
        if v == "0":
            return ("b", False)
        if v == "1":
            return ("b", True)
        raise PackageError(f"{where}: bool member v must be 0 or 1, got {v!r}")
    if kind == "n":
        return ("n", parse_decimal(v, where=where))
    if kind == "s":
        return ("s", v)
    raise PackageError(f"{where}: unknown member kind {kind!r}")


def member_to_json_obj(member) -> dict:
    """JSON object form used inside pivot axis keys: {"kind":..,"v":..}."""
    kind, v = member_kind_v(member)
    obj = {"kind": kind}
    if v is not None:
        obj["v"] = v
    return obj


def member_from_json_obj(obj, *, where: str = "axis key"):
    """Inverse of :func:`member_to_json_obj` (tolerant of a null v)."""
    if not isinstance(obj, dict):
        raise PackageError(f"{where}: member must be an object, got {obj!r}")
    kind = obj.get("kind")
    if kind not in KINDS:
        raise PackageError(f"{where}: unknown member kind {kind!r}")
    v = obj.get("v")
    if v is not None and not isinstance(v, str):
        # Numbers occasionally appear as raw JSON numbers in hand-made
        # packages; normalise them through the decimal parser.
        if isinstance(obj["v"], bool):
            raise PackageError(f"{where}: member v must be a string, got {obj['v']!r}")
        if isinstance(obj["v"], (int, float)):
            v = canon_decimal(member_from_value(obj["v"], where=where)[1])
        else:
            raise PackageError(f"{where}: member v must be a string, got {obj['v']!r}")
    return member_from_kind_v(kind, v, where=where)


def axis_key_json(members) -> str:
    """Deterministic JSON encoding of an axis key (array of members)."""
    return json.dumps([member_to_json_obj(m) for m in members])


def axis_key_parse(text: str, *, where: str = "axis key"):
    """Parse a JSON axis key back into a tuple of typed members."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PackageError(f"{where}: invalid JSON: {text!r}") from exc
    if not isinstance(data, list):
        raise PackageError(f"{where}: axis key must be a JSON array")
    return tuple(member_from_json_obj(o, where=where) for o in data)


def coerce_member(obj, *, where: str = "selection item"):
    """Accept a member tuple, a {"kind","v"} dict, or a raw scalar."""
    if isinstance(obj, tuple) and len(obj) == 2 and obj[0] in KINDS:
        kind, value = obj
        if kind == "m":
            if value is not None:
                raise SourceShapeError(f"{where}: 'm' member must not carry a value")
            return ("m", None)
        if kind == "b":
            if not isinstance(value, bool):
                raise SourceShapeError(f"{where}: 'b' member needs a bool, got {value!r}")
            return ("b", value)
        if kind == "n":
            member = member_from_value(value, where=where)
            if member[0] != "n":
                raise SourceShapeError(f"{where}: 'n' member needs a number, got {value!r}")
            return member
        if not isinstance(value, str):
            raise SourceShapeError(f"{where}: 's' member needs a string, got {value!r}")
        return ("s", value)
    if isinstance(obj, dict):
        return member_from_json_obj(obj, where=where)
    return member_from_value(obj, where=where)
