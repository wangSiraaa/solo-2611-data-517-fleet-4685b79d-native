"""Error types for pivotpack.

The hierarchy deliberately separates three failure domains:

* :class:`PackageError` -- the workbook package on disk is illegal
  (unknown indices/fields, duplicate typed identities, broken
  relationships, path traversal, ...).  Raised while *reading*.
* :class:`SourceShapeError` -- the new source data does not match the
  required rectangular shape / column set / value domain.  Raised while
  *refreshing*, before anything is written.
* :class:`MeasureError` -- a non-null, non-numeric value was found in a
  measure column that a pivot actually consumes.  Raised while
  *recomputing*, before anything is written.

All three derive from :class:`PivotPackError` so callers can catch one
base class.  Lookup failures for the public API use ``Unknown*Error``.
"""


class PivotPackError(Exception):
    """Base class for every error raised by pivotpack."""


class PackageError(PivotPackError):
    """The workbook package violates the restricted format."""


class SourceShapeError(PivotPackError):
    """The refresh source is malformed or changes the column set."""


class MeasureError(PivotPackError):
    """A consumed measure value is non-null and non-numeric."""


class UnknownCacheError(PivotPackError):
    """No cache part with the requested cache_id."""


class UnknownPivotError(PivotPackError):
    """No pivot part with the requested id."""


class UnknownSlicerError(PivotPackError):
    """No slicer part with the requested id."""
