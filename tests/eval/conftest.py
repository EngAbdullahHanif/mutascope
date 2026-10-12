"""A toy ProteinGym release for the harness: reference, precomputed scores, leaderboard files.

Three assays: ``A1`` and ``A2`` (protein P1, Stability) and ``B1`` (P2, Binding). ``B1``'s
single mutants all have label 1, like ``F7YBW8_MESOW_Aakre_2015``, so AUC and MCC are
undefined there on single mutants but defined on all rows (its multi-mutant has label 0).
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest


def _assay(n: int, seed: int, all_positive: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dms = rng.normal(size=n)
    singles = pd.DataFrame(
        {
            "mutant": [f"M{i + 1}A" for i in range(n)],
            "DMS_score": dms,
            "DMS_score_bin": 1 if all_positive else (dms >= np.median(dms)).astype(int),
            "ESM2_650M": dms + rng.normal(scale=1.0, size=n),
            "SaProt_650M_AF2": dms + rng.normal(scale=0.5, size=n),
        }
    )
    multi = pd.DataFrame(
        {
            "mutant": ["M1A:M2A"],
            "DMS_score": [-5.0],
            "DMS_score_bin": [0],
            "ESM2_650M": [-4.0],
            "SaProt_650M_AF2": [-4.5],
        }
    )
    return pd.concat([singles, multi], ignore_index=True)


ASSAYS = {"A1": _assay(40, 1), "A2": _assay(30, 2), "B1": _assay(20, 3, all_positive=True)}


@pytest.fixture
def release_dir(tmp_path: Path) -> Path:
    """``reference.csv``, ``scores/assay_id=<id>/data.parquet`` and two leaderboard CSVs."""
    pd.DataFrame(
        {
            "DMS_id": ["A1", "A2", "B1"],
            "UniProt_ID": ["P1", "P1", "P2"],
            "coarse_selection_type": ["Stability", "Stability", "Binding"],
        }
    ).to_csv(tmp_path / "reference.csv", index=False)
    with duckdb.connect() as con:
        for assay_id, frame in ASSAYS.items():
            part = tmp_path / "scores" / f"assay_id={assay_id}"
            part.mkdir(parents=True)
            con.from_df(frame).write_parquet(str(part / "data.parquet"))
    pd.DataFrame(
        {
            "Model_rank": [1, 2],
            "Model_name": ["SaProt (650M)", "ESM2 (650M)"],
            "Average_Spearman": [0.5, 0.4],
            "Function_Binding": [0.45, 0.35],
            "Function_Stability": [0.55, 0.45],
        }
    ).to_csv(tmp_path / "summary.csv", index=False)
    pd.DataFrame(
        {
            "DMS ID": ["A1", "A2", "B1"],
            "ESM2 (650M)": [0.4, 0.5, 0.3],
            "SaProt (650M)": [0.6, 0.5, 0.4],
            "Other": [0.0, 0.0, 0.0],
        }
    ).to_csv(tmp_path / "assay_level.csv", index=False)
    return tmp_path
