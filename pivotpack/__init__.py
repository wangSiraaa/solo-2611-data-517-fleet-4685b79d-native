"""pivotpack -- cache refresh and report rebinding for the restricted
ZIP/XML workbook package defined by materials/517/format.md.

Public entry points:

* :class:`Cache` -- independent cache model: ``Cache.from_part`` /
  ``Cache.refresh`` / ``Cache.read_records`` / ``Cache.to_part``.
* :class:`Workbook` -- package model: ``Workbook.open`` /
  ``refresh_cache`` / ``read_pivot`` / ``set_selection`` /
  ``slicer_state`` / ``get_cache``.

Typed members are ``(kind, value)`` tuples with kind in
``{"m", "b", "n", "s"}``; see :mod:`pivotpack.typed`.
"""

from .cache import Cache, Field
from .errors import (
    MeasureError,
    PackageError,
    PivotPackError,
    SourceShapeError,
    UnknownCacheError,
    UnknownPivotError,
    UnknownSlicerError,
)
from .typed import (
    MISSING,
    axis_key_json,
    axis_key_parse,
    canon_decimal,
    coerce_member,
    exact_sum,
    member_from_value,
    member_kind_v,
)
from .workbook import (
    PivotResult,
    RefreshReport,
    SlicerState,
    Workbook,
    compute_cells,
    load_source,
)

__all__ = [
    "Cache",
    "Field",
    "Workbook",
    "PivotResult",
    "SlicerState",
    "RefreshReport",
    "compute_cells",
    "load_source",
    "MISSING",
    "axis_key_json",
    "axis_key_parse",
    "canon_decimal",
    "coerce_member",
    "exact_sum",
    "member_from_value",
    "member_kind_v",
    "PivotPackError",
    "PackageError",
    "SourceShapeError",
    "MeasureError",
    "UnknownCacheError",
    "UnknownPivotError",
    "UnknownSlicerError",
]
