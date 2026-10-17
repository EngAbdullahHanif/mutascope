"""Schemas of the export's results tables (``results/export/``)."""

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

FIDELITY_METRICS = ("spearman", "coverage_80", "coverage_90")
FIDELITY_LEVELS = ("assay", "aggregate")


class BundleFidelitySchema(pa.DataFrameModel):
    """A number recomputed from the written bundle, against the unrounded source and the
    reported value (ADR 0028).

    ``source``: from the values the bundle was encoded from; ``encoded``: from the decoded
    protein files; ``reported``: the results table's value where the bundle holds the same
    numbers it was computed from (null otherwise, e.g. a landscape whose benchmark region was
    part of the target). An ``aggregate`` row (``assay_id`` null) is the ProteinGym aggregate
    of Spearman, or the mean coverage over assays. ``passed``: both differences within
    ``tolerance``.
    """

    assay_id: Series[str] = pa.Field(nullable=True)
    model: Series[str]
    metric: Series[str] = pa.Field(isin=list(FIDELITY_METRICS))
    level: Series[str] = pa.Field(isin=list(FIDELITY_LEVELS))
    n_assays: Series[int] = pa.Field(ge=1)
    source: Series[float] = pa.Field(nullable=True)
    encoded: Series[float] = pa.Field(nullable=True)
    reported: Series[float] = pa.Field(nullable=True)
    diff_source: Series[float] = pa.Field(ge=0)
    diff_reported: Series[float] = pa.Field(ge=0, nullable=True)
    tolerance: Series[float] = pa.Field(gt=0)
    passed: Series[bool]

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "model", "metric", "level"]  # noqa: RUF012 — read by pandera

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def assay_iff_level(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """An assay id exactly on assay rows."""
        return (df["assay_id"].notna() == (df["level"] == "assay")).astype(bool)


class BundleSizeSchema(pa.DataFrameModel):
    """One protein file's size: gzip bytes (what the browser downloads) and JSON bytes."""

    assay_id: Series[str] = pa.Field(unique=True)
    length: Series[int] = pa.Field(ge=1)
    supervised: Series[bool]
    bytes: Series[int] = pa.Field(ge=1)
    raw_bytes: Series[int] = pa.Field(ge=1)
    budget_bytes: Series[int] = pa.Field(ge=1)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
