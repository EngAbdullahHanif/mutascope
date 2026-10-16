"""The final results table's pieces (ADR 0025): comparators, size bins, by-size differences."""

import numpy as np
import pandas as pd
import pytest

from mutascope.eval.benchmark import BootstrapParams
from mutascope.eval.final import (
    assay_differences,
    comparator,
    size_bin,
    size_bin_labels,
    size_comparisons,
)

COMPARATORS = {"structure": "zero_shot_saprot_score", "sequence": "zero_shot_esm2_score"}


def pooled(model: str, values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "model": model,
            "assay_id": list(values),
            "split": pd.Series(["random"] * len(values), dtype="string"),
            "fold": pd.Series([pd.NA] * len(values), dtype="Int64"),
            "metric": "spearman",
            "value": list(values.values()),
        }
    )


def test_comparator_is_saprot_with_structure_else_esm2() -> None:
    assert comparator(True, COMPARATORS) == "zero_shot_saprot_score"
    assert comparator(False, COMPARATORS) == "zero_shot_esm2_score"


def test_size_bins() -> None:
    edges = [200, 300, 1000]
    assert size_bin_labels(edges) == ["<200", "200-299", "300-999", ">=1000"]
    assert [size_bin(n, edges) for n in (199, 200, 299, 300, 999, 1000)] == [
        "<200", "200-299", "200-299", "300-999", "300-999", ">=1000",
    ]  # fmt: skip
    with pytest.raises(ValueError, match="ascending"):
        size_bin_labels([300, 200])


def test_differences_and_size_comparisons() -> None:
    runs = {
        "ridge_seq_struct": pooled("ridge_seq_struct", {"A": 0.5, "B": 0.6, "C": 0.9}),
        "zero_shot_saprot_score": pooled(
            "zero_shot_saprot_score", {"A": 0.4, "B": 0.3, "C": 0.5, "D": 0.2}
        ),
    }
    sizes = {"A": 250, "B": 260, "C": 5000, "D": 100}
    pairs = [("ridge_seq_struct", "zero_shot_saprot_score")]
    differences = assay_differences(runs, pairs, sizes, [300])
    assert differences["assay_id"].tolist() == ["A", "B", "C"]  # only the shared assays
    assert differences["size_bin"].tolist() == ["<300", "<300", ">=300"]
    np.testing.assert_allclose(differences["diff"], [0.1, 0.3, 0.4])
    table = size_comparisons(differences, BootstrapParams(2000, 0.95), 0, [300]).set_index(
        "size_bin"
    )
    assert table.loc["<300", "diff"] == pytest.approx(0.2)  # plain mean of 0.1 and 0.3
    assert table.loc["<300", "n_assays"] == 2
    low, high = float(str(table.loc["<300", "ci_low"])), float(str(table.loc["<300", "ci_high"]))
    assert 0.1 - 1e-12 <= low <= 0.2 <= high <= 0.3 + 1e-12
    assert table.loc[">=300", "diff"] == pytest.approx(0.4)
    assert table.loc[">=300", "share_a_better"] == 1.0
