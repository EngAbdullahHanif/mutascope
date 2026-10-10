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
