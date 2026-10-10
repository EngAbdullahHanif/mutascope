"""Summary tables of the processed ProteinGym data, computed in DuckDB.

Everything here reads the views created by :func:`mutascope.data.db.connect` (``variants``,
``cleaning_report``, ``assays`` and, for structure coverage, ``structure_features``) and returns
small pandas frames: one row per assay, per function type or per histogram bin. The notebook
only displays them, and the data card's counts come from the same functions.

Score histograms are binned here rather than in the browser: 696,311 rows are too many to ship
to a chart, and binning in SQL keeps the notebook fast.
"""

from pathlib import Path

import duckdb
import pandas as pd

from mutascope.data.db import connect
from mutascope.utils.config import load_config

MAX_MODEL_LENGTH = 1022
"""Longest sequence ESM-2 scores in one pass; longer targets need sliding windows."""


def connect_processed(
    config_dir: Path, root: Path, overrides: list[str] | None = None
) -> duckdb.DuckDBPyConnection:
    """Open the DuckDB views over the processed data at the paths in the Hydra config.

    Args:
        config_dir: Hydra config directory (``conf/``).
        root: Directory that relative config paths are resolved against (the repo root), so
            the result does not depend on where a notebook was launched from.
        overrides: Hydra overrides, e.g. ``["data.data_dir=/scratch/data"]``.
    """
    data = load_config(config_dir, overrides).data
    processed = root / str(data.processed_dir)
    reports = root / str(data.reports_dir)
    return connect(
        processed / "variants",
        report_parquet=reports / "cleaning_report.parquet",
        reference_csv=root / str(data.raw_dir) / str(data.files.reference.filename),
        structure_features_dir=processed / "structure_features",
        three_di_dir=processed / "three_di",
    )


def assay_overview(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per assay with its size, function type, sequence length and score summary.

    ``coverage`` is the share of all ``19 * seq_len`` possible single substitutions that were
    measured. ``frac_fit`` is the share labelled 1 by ProteinGym's binarization, which splits
    at ``bin_cutoff`` (chosen by ``bin_method``: the score median or a manual threshold).
    ``positions_with_structure`` counts residues of the target that have structure features
    (0 for the documented exceptions).
    """
    return con.sql(
        f"""
        WITH scores AS (
            SELECT assay_id,
                   count(*) AS singles,
                   count(DISTINCT position) AS positions_mutated,
                   avg(DMS_score_bin) AS frac_fit,
                   min(DMS_score) AS score_min,
                   median(DMS_score) AS score_median,
                   max(DMS_score) AS score_max,
                   stddev_samp(DMS_score) AS score_sd
            FROM variants GROUP BY assay_id
        ), structure AS (
            SELECT assay_id, count(*) AS positions_with_structure
            FROM structure_features GROUP BY assay_id
        )
        SELECT a.assay_id,
               a.UniProt_ID AS uniprot_id,
               a.coarse_selection_type AS function_type,
               a.taxon,
               a.seq_len,
               a.seq_len > {MAX_MODEL_LENGTH} AS needs_windows,
               r.rows_in,
               r.multi_mutant AS multi_mutants,
               s.singles,
               s.positions_mutated,
               s.singles / (19.0 * a.seq_len) AS coverage,
               s.frac_fit,
               a.DMS_binarization_cutoff AS bin_cutoff,
               a.DMS_binarization_method AS bin_method,
               s.score_min, s.score_median, s.score_max, s.score_sd,
               coalesce(st.positions_with_structure, 0) AS positions_with_structure
        FROM assays a
        JOIN cleaning_report r USING (assay_id)
        JOIN scores s USING (assay_id)
        LEFT JOIN structure st USING (assay_id)
        ORDER BY a.assay_id
        """
    ).df()


def function_type_summary(overview: pd.DataFrame) -> pd.DataFrame:
    """Assays, distinct proteins and single mutants per function type, largest first.

    The function type (``coarse_selection_type``) is ProteinGym's second aggregation level, so
    its imbalance is what the headline metric averages over.
    """
    grouped = overview.groupby("function_type")
    summary = pd.DataFrame(
        {
            "assays": grouped["assay_id"].count(),
            "proteins": grouped["uniprot_id"].nunique(),
            "singles": grouped["singles"].sum(),
        }
    ).reset_index()
    return summary.sort_values("assays", ascending=False, ignore_index=True)


def standardized_score_histogram(
    con: duckdb.DuckDBPyConnection, *, bins: int = 60, clip: float = 5.0
) -> pd.DataFrame:
    """Density of per-assay z-scored ``DMS_score``, per function type.

    Raw scores are on different scales in every assay (log enrichment, ΔΔG, growth rates…), so
    pooling them is meaningless; z-scoring within each assay makes their shapes comparable.
    Values are clipped to ``[-clip, clip]`` so long tails land in the edge bins.

    Returns:
        Columns ``function_type``, ``bin_start``, ``bin_end``, ``density`` (each function
        type's densities integrate to 1).
    """
    width = 2 * clip / bins
    return con.sql(
        f"""
        WITH z AS (
            SELECT v.assay_id, a.coarse_selection_type AS function_type,
                   (v.DMS_score - avg(v.DMS_score) OVER w) / stddev_samp(v.DMS_score) OVER w
                       AS z
            FROM variants v JOIN assays a USING (assay_id)
            WINDOW w AS (PARTITION BY v.assay_id)
        ), binned AS (
            SELECT function_type,
                   least(floor((greatest(least(z, {clip}), -{clip}) + {clip}) / {width}),
                         {bins - 1}) AS bin
            FROM z WHERE z IS NOT NULL AND isfinite(z)
        )
        SELECT function_type,
               -{clip} + bin * {width} AS bin_start,
               -{clip} + (bin + 1) * {width} AS bin_end,
               count(*) / (sum(count(*)) OVER (PARTITION BY function_type) * {width}) AS density
        FROM binned GROUP BY function_type, bin
        ORDER BY function_type, bin
        """
    ).df()


def assay_score_histogram(
    con: duckdb.DuckDBPyConnection, assay_id: str, *, bins: int = 40
) -> pd.DataFrame:
    """Counts of one assay's raw ``DMS_score``, split by its binary label.

    Shows where ProteinGym's binarization cutoff falls in the score distribution.

    Returns:
        Columns ``bin_start``, ``bin_end``, ``label`` (``"fit (1)"`` / ``"unfit (0)"``),
        ``count``. Empty if the assay is unknown.
    """
    return con.execute(
        """
        WITH s AS (SELECT DMS_score, DMS_score_bin FROM variants WHERE assay_id = $assay),
        r AS (SELECT min(DMS_score) AS lo,
                     greatest(max(DMS_score) - min(DMS_score), 1e-12) / $bins AS width
              FROM s),
        binned AS (
            SELECT least(floor((s.DMS_score - r.lo) / r.width), $bins - 1) AS bin,
                   s.DMS_score_bin, r.lo, r.width
            FROM s, r
        )
        SELECT any_value(lo) + bin * any_value(width) AS bin_start,
               any_value(lo) + (bin + 1) * any_value(width) AS bin_end,
               CASE WHEN DMS_score_bin = 1 THEN 'fit (1)' ELSE 'unfit (0)' END AS label,
               count(*) AS count
        FROM binned GROUP BY bin, DMS_score_bin ORDER BY bin, label
        """,
        {"assay": assay_id, "bins": bins},
    ).df()
