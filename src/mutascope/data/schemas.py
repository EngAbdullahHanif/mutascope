"""pandera schemas for the variant tables.

``RawVariantSchema`` describes one ProteinGym assay CSV as published. It is not strict: upstream
files may carry extra columns (``ANCSZ_Hobbs_2022`` has a duplicate ``DMS_bin_score``), which the
checker reports rather than rejecting. ``ProcessedVariantSchema`` describes the single-mutant
table MutaScope itself writes, so it is strict: exactly these columns, nothing else.

Column names follow ProteinGym (``DMS_score``) via aliases; attributes are snake_case.
"""

import pandas as pd
import pandera.pandas as pa
from pandera.typing.pandas import Series

from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import (
    MUTANT_PATTERN,
    SINGLE_MUTATION_PATTERN,
    MutationParseError,
    parse_mutation,
)

# pandera's str_matches uses re.match (anchored at the start only); \Z anchors the end.
_MUTANT_FULL = rf"(?:{MUTANT_PATTERN})\Z"
_SINGLE_FULL = rf"(?:{SINGLE_MUTATION_PATTERN})\Z"
_SEQUENCE_FULL = rf"[{AA_ORDER}]+\Z"


class RawVariantSchema(pa.DataFrameModel):
    """One ProteinGym DMS substitution CSV, read with every column as text.

    Numeric columns are coerced, so a malformed value is reported with its row instead of
    failing the whole read.
    """

    mutant: Series[str] = pa.Field(str_matches=_MUTANT_FULL)
    mutated_sequence: Series[str] = pa.Field(str_matches=_SEQUENCE_FULL)
    dms_score: Series[float] = pa.Field(alias="DMS_score", coerce=True)
    """Higher = fitter (ProteinGym convention). Never sign-flipped."""
    dms_score_bin: Series[int] = pa.Field(alias="DMS_score_bin", coerce=True, isin=[0, 1])

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = False


class ProcessedVariantSchema(pa.DataFrameModel):
    """Single-mutant variants after cleaning, one row per (assay, mutant).

    ``position`` is 0-indexed and, with ``wt_aa`` / ``mt_aa``, must equal what
    :func:`~mutascope.data.parse.parse_mutation` returns for ``mutant``: the derived columns can
    never drift from the string they came from.
    """

    assay_id: Series[str]
    mutant: Series[str] = pa.Field(str_matches=_SINGLE_FULL)
    position: Series[int] = pa.Field(ge=0)
    wt_aa: Series[str] = pa.Field(isin=list(AA_ORDER))
    mt_aa: Series[str] = pa.Field(isin=list(AA_ORDER))
    dms_score: Series[float] = pa.Field(alias="DMS_score")
    dms_score_bin: Series[int] = pa.Field(alias="DMS_score_bin", isin=[0, 1])

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        unique = ["assay_id", "mutant"]  # noqa: RUF012 — pandera reads this class attribute

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def wt_differs_from_mt(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """A synonymous row is not a substitution."""
        return (df["wt_aa"] != df["mt_aa"]).astype(bool)

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def derived_columns_match_mutant(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """``position``, ``wt_aa``, ``mt_aa`` agree with parsing ``mutant``."""
        expected = df["mutant"].map(_parse_or_none)
        actual = list(zip(df["wt_aa"], df["position"], df["mt_aa"], strict=True))
        matches: list[bool] = [e == a for e, a in zip(expected, actual, strict=True)]
        return pd.Series(matches, index=df.index, dtype=bool)


def _parse_or_none(text: str) -> tuple[str, int, str] | None:
    """Parsed (wt, position, mt), or None so the row fails the check instead of raising."""
    try:
        sub = parse_mutation(text)
    except MutationParseError:
        return None
    return sub.wt, sub.position, sub.mt


DROP_REASONS = ("multi_mutant", "missing_value", "duplicate", "conflicting_duplicate")
"""Why ``mutascope data clean`` removes a row, in the order the filters apply.

Each removed row is counted under exactly one reason (the first that applies), so
``rows_in == rows_out + sum(reasons)`` for every assay.
"""


class CleaningReportSchema(pa.DataFrameModel):
    """One row per assay: rows read, rows written, and rows removed per reason."""

    assay_id: Series[str] = pa.Field(unique=True)
    rows_in: Series[int] = pa.Field(ge=0)
    multi_mutant: Series[int] = pa.Field(ge=0)
    missing_value: Series[int] = pa.Field(ge=0)
    duplicate: Series[int] = pa.Field(ge=0)
    conflicting_duplicate: Series[int] = pa.Field(ge=0)
    rows_out: Series[int] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def rows_add_up(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Every row read is either written or counted under one drop reason."""
        dropped = df[list(DROP_REASONS)].sum(axis=1)
        return (df["rows_in"] == df["rows_out"] + dropped).astype(bool)


SSE_CODES = ("a", "b", "c")
"""P-SEA secondary structure states as biotite returns them: helix, strand, coil."""

THREE_DI_ALPHABET = AA_ORDER
"""Foldseek's 20 3Di states are written with the same 20 letters as the amino acids."""


class StructureFeatureSchema(pa.DataFrameModel):
    """Per-residue features of an assay's AF2 structure, one row per (assay, position).

    ``position`` is the 0-based index into the assay's ``target_seq``, so the table joins with
    :class:`ProcessedVariantSchema` on ``(assay_id, position)``. A structure covering only a
    window of the target has rows for that window only.
    """

    assay_id: Series[str]
    position: Series[int] = pa.Field(ge=0)
    residue: Series[str] = pa.Field(isin=list(AA_ORDER))
    """``target_seq[position]``; checked against the structure's residue before writing."""
    plddt: Series[float] = pa.Field(ge=0, le=100)
    sasa: Series[float] = pa.Field(ge=0)
    """Solvent-accessible surface area of the residue's heavy atoms, in Å²."""
    rel_sasa: Series[float] = pa.Field(ge=0)
    """``sasa`` over the residue's theoretical maximum. Not clipped: exposed termini exceed 1."""
    sse: Series[str] = pa.Field(isin=list(SSE_CODES))
    contact_count: Series[int] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "position"]  # noqa: RUF012 — pandera reads this class attribute

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def contiguous_positions(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """A structure models one unbroken stretch of the target."""
        return _contiguous_per_assay(df)


class ThreeDiSchema(pa.DataFrameModel):
    """Foldseek 3Di state of each residue of an assay's AF2 structure.

    Same ``(assay_id, position)`` keys as :class:`StructureFeatureSchema`. The per-assay 3Di
    string is ``three_di`` ordered by ``position``.
    """

    assay_id: Series[str]
    position: Series[int] = pa.Field(ge=0)
    residue: Series[str] = pa.Field(isin=list(AA_ORDER))
    """``target_seq[position]``; checked against Foldseek's own amino-acid output."""
    three_di: Series[str] = pa.Field(isin=list(THREE_DI_ALPHABET))

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "position"]  # noqa: RUF012 — pandera reads this class attribute

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def contiguous_positions(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """A structure models one unbroken stretch of the target."""
        return _contiguous_per_assay(df)


def _contiguous_per_assay(df: pd.DataFrame) -> "pd.Series[bool]":
    """True on every row of an assay whose positions are exactly ``min..max`` with no gaps."""
    groups = df.groupby("assay_id")["position"]
    span = groups.transform("max") - groups.transform("min") + 1
    return (span == groups.transform("size")).astype(bool)


STRUCTURE_STATUSES = ("features", "exception")


class StructureReportSchema(pa.DataFrameModel):
    """One row per assay: what the structure steps produced for it, or why nothing.

    Exceptions (``status == "exception"``) have no structure columns, only their reasons.
    """

    assay_id: Series[str] = pa.Field(unique=True)
    status: Series[str] = pa.Field(isin=list(STRUCTURE_STATUSES))
    pdb_file: Series[str] = pa.Field(nullable=True)
    range_start: Series[pd.Int64Dtype] = pa.Field(ge=1, nullable=True)
    """1-based inclusive ``pdb_range`` as published."""
    range_end: Series[pd.Int64Dtype] = pa.Field(ge=1, nullable=True)
    target_length: Series[int] = pa.Field(ge=1)
    residues: Series[pd.Int64Dtype] = pa.Field(ge=1, nullable=True)
    mean_plddt: Series[float] = pa.Field(ge=0, le=100, nullable=True)
    frac_low_plddt: Series[float] = pa.Field(ge=0, le=1, nullable=True)
    frac_helix: Series[float] = pa.Field(ge=0, le=1, nullable=True)
    frac_strand: Series[float] = pa.Field(ge=0, le=1, nullable=True)
    frac_coil: Series[float] = pa.Field(ge=0, le=1, nullable=True)
    mean_rel_sasa: Series[float] = pa.Field(ge=0, nullable=True)
    mean_contact_count: Series[float] = pa.Field(ge=0, nullable=True)
    variants: Series[int] = pa.Field(ge=0)
    variants_with_structure: Series[int] = pa.Field(ge=0)
    documented_reason: Series[str] = pa.Field(nullable=True)
    observed_failure: Series[str] = pa.Field(nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def exceptions_have_no_structure(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """An exception has a reason and no residues or covered variants; others the reverse."""
        exception = df["status"] == "exception"
        return (
            exception
            & df["residues"].isna()
            & (df["variants_with_structure"] == 0)
            & df["documented_reason"].notna()
        ) | (~exception & df["residues"].notna() & df["documented_reason"].isna())

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def sse_fractions_sum_to_one(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        """Every residue gets exactly one P-SEA state."""
        total = df[["frac_helix", "frac_strand", "frac_coil"]].sum(axis=1)
        return (df["residues"].isna() | ((total - 1).abs() < 1e-9)).astype(bool)
