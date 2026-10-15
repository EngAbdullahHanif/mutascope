"""pandera schemas for the feature store (``processed/features``) and its run reports (ADR 0020).

The store has one partition per assay and three parts:

- ``variants``: every possible single substitution of the target, 19 per residue, with the
  per-variant scalar features (:class:`FeatureVariantSchema`);
- ``positions``: one row per residue, with the structure features and the region each model's
  per-residue arrays were read from (:class:`FeaturePositionSchema`);
- ``arrays``: per-residue matrices, one ``.npy`` per model and kind (``(L, 20)`` log-probabilities,
  ``(L, hidden)`` embeddings), row ``i`` = residue ``i`` of the target.

Per-model columns are named ``score_<model>`` and ``region_<model>``, so the schemas match them
by pattern. A model column is NaN or null for a whole assay where that model has no input (a
structure-aware model on an assay without a structure), never for some residues of it.
"""

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.typing.pandas import Series

from mutascope.alphabet import AA_ORDER

SUBSTITUTIONS_PER_RESIDUE = len(AA_ORDER) - 1


class FeatureVariantSchema(pa.DataFrameModel):
    """One possible substitution ``wt_aa -> mt_aa`` at ``position`` (0-indexed) of a target.

    Rows are ordered by position, then by ``mt_aa`` in ``AA_ORDER``, with the wild type left
    out: 19 rows per residue. There is no label column; the store never reads labels.
    """

    assay_id: Series[str]
    position: Series[int] = pa.Field(ge=0)
    wt_aa: Series[str] = pa.Field(isin=list(AA_ORDER))
    mt_aa: Series[str] = pa.Field(isin=list(AA_ORDER))
    rel_position: Series[np.float32] = pa.Field(ge=0, le=1)
    blosum62: Series[np.float32]
    score: Series[np.float32] = pa.Field(alias="^score_.+$", regex=True, nullable=True)
    """``log P(mt) - log P(wt)``, masked marginal, per model."""

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        unique = ["assay_id", "position", "mt_aa"]  # noqa: RUF012 — read by pandera

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def mutant_differs_from_wild_type(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        return df["wt_aa"] != df["mt_aa"]

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def nineteen_per_residue(cls, df: pd.DataFrame) -> bool:
        counts = df.groupby(["assay_id", "position"]).size()
        return bool((counts == SUBSTITUTIONS_PER_RESIDUE).all())

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def a_model_score_is_missing_for_a_whole_assay_or_not_at_all(cls, df: pd.DataFrame) -> bool:
        scores = [c for c in df.columns if c.startswith("score_")]
        missing = df[scores].isna().groupby(df["assay_id"]).agg(["all", "any"])
        return all((missing[(c, "all")] == missing[(c, "any")]).all() for c in scores)


class FeaturePositionSchema(pa.DataFrameModel):
    """One residue of a target: its structure features and where its model arrays came from.

    ``plddt``, ``rel_sasa`` and ``contact_count`` are NaN where the assay's structure does not
    model the residue (outside ``pdb_range``, or no structure at all). ``region_<model>`` is the
    label of the cache entry the residue's log-probabilities and embedding were read from: the
    benchmark region where the residue is inside it, else ``full`` (ADR 0020); null where the
    model has no input for the assay.
    """

    assay_id: Series[str]
    position: Series[int] = pa.Field(ge=0)
    wt_aa: Series[str] = pa.Field(isin=list(AA_ORDER))
    plddt: Series[np.float32] = pa.Field(ge=0, le=100, nullable=True)
    rel_sasa: Series[np.float32] = pa.Field(ge=0, nullable=True)
    contact_count: Series[np.float32] = pa.Field(ge=0, nullable=True)
    region: Series[str] = pa.Field(alias="^region_.+$", regex=True, nullable=True)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        unique = ["assay_id", "position"]  # noqa: RUF012 — read by pandera

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def contiguous_from_zero(cls, df: pd.DataFrame) -> bool:
        return all(
            (group.to_numpy() == np.arange(len(group))).all()
            for _, group in df.groupby("assay_id")["position"]
        )

    @pa.dataframe_check  # pyright: ignore[reportUnknownMemberType] — untyped in pandera
    @classmethod
    def structure_features_are_missing_together(cls, df: pd.DataFrame) -> "pd.Series[bool]":
        missing = df[["plddt", "rel_sasa", "contact_count"]].isna()
        return missing.all(axis=1) | ~missing.any(axis=1)


class FeatureStoreReportSchema(pa.DataFrameModel):
    """One assay of the feature store: its size and which inputs it has."""

    assay_id: Series[str] = pa.Field(unique=True)
    target_length: Series[int] = pa.Field(ge=1)
    variants: Series[int] = pa.Field(ge=SUBSTITUTIONS_PER_RESIDUE)
    measured: Series[int] = pa.Field(ge=0)
    """Processed single mutants of the assay, all of which are in the store."""
    structure: Series[bool]
    """The assay has a structure (structure features and the structure-aware model)."""
    residues_with_structure: Series[int] = pa.Field(ge=0)
    residues_from_full_target: Series[int] = pa.Field(ge=0)
    """Residues outside some model's partial benchmark region, whose arrays for that model come
    from its full-target landscape (ADR 0020). No measured variant is at one of them."""
    bytes: Series[int] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True


class EmbeddingRunSchema(pa.DataFrameModel):
    """What one ``mutascope features embed`` invocation did for one landscape of one assay.

    ``computed`` is false when the cache had the embedding, or an earlier row of the same
    invocation computed the same cache entry (assays of one protein share it).
    """

    assay_id: Series[str]
    model: Series[str]
    region: Series[str]
    region_length: Series[int] = pa.Field(ge=1)
    benchmark: Series[bool]
    full_target: Series[bool]
    computed: Series[bool]
    seconds: Series[float] = pa.Field(ge=0)

    class Config:  # pyright: ignore[reportIncompatibleVariableOverride]
        strict = True
        ordered = True
        unique = ["assay_id", "region"]  # noqa: RUF012 — read by pandera
