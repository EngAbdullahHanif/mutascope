"""Helpers of the ONNX checks and timings (ADR 0029)."""

import shutil
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.eval.onnx import (
    landscape_agreement,
    max_abs_diff,
    parity_inputs,
    random_sequence,
    time_calls,
)
from mutascope.scoring.base import FloatArray
from mutascope.scoring.marginals import MarginalScorer, MaskedPass, ModelSettings
from mutascope.scoring.windows import Window

CONF = Path(__file__).parents[2] / "conf"


def test_parity_inputs_mask_one_residue_per_row_from_first_to_last() -> None:
    tokens = np.array([0, *range(5, 25), 2], dtype=np.int64)  # <cls>, 20 residues, <eos>
    inputs = parity_inputs(tokens, mask_id=32, masked=4)
    np.testing.assert_array_equal(inputs["wt"], tokens[None, :])
    masked = inputs["masked"]
    assert masked.shape == (4, len(tokens))
    rows, columns = np.nonzero(masked == 32)
    np.testing.assert_array_equal(rows, [0, 1, 2, 3])
    np.testing.assert_array_equal(columns, [1, 7, 14, 20])  # residues 0 and 19 included
    assert (masked[:, [0, -1]] == [0, 2]).all()  # <cls> and <eos> never masked
    short = parity_inputs(tokens[:4], mask_id=32, masked=8)["masked"]  # 2 residues
    assert short.shape == (2, 4)


def test_landscape_agreement_leaves_out_the_wild_type_column() -> None:
    seq = "ACD"
    rng = np.random.default_rng(0)
    a = rng.normal(size=(3, 20)).astype(np.float32)
    wt = [AA_ORDER.index(r) for r in seq]
    a[np.arange(3), wt] = 0
    b = a.copy()
    b[np.arange(3), wt] = 100.0  # differs only where the comparison must not look
    rho, diff = landscape_agreement(a, b, seq)
    assert rho == pytest.approx(1.0) and diff == 0.0
    a[0, 1] = np.nan
    with pytest.raises(ValueError, match="unscored"):
        landscape_agreement(a, b, seq)
    with pytest.raises(ValueError, match="shape"):
        landscape_agreement(a[:2], b[:2], seq)


def test_max_abs_diff_and_timings() -> None:
    assert max_abs_diff(np.zeros((2, 2), np.float32), np.full((2, 2), -0.5, np.float32)) == 0.5
    with pytest.raises(ValueError, match="shapes"):
        max_abs_diff(np.zeros(2, np.float32), np.zeros(3, np.float32))
    calls: list[int] = []
    seconds = time_calls(lambda: calls.append(1), warmup=2, repeats=3)
    assert len(seconds) == 3 and len(calls) == 5 and all(s >= 0 for s in seconds)
    seq = random_sequence(50, np.random.default_rng(0))
    assert len(seq) == 50 and set(seq) <= set(AA_ORDER)


class _Planner(MarginalScorer[None]):
    """The pass planner of :class:`MarginalScorer`, without a runtime."""

    def encode(self, seq: str, struct_tokens: str | None) -> None:
        raise NotImplementedError

    def _masked_marginal(self, tokens: None, residues: Sequence[int]) -> FloatArray:
        raise NotImplementedError

    def _wt_marginal(self, tokens: None) -> FloatArray:
        raise NotImplementedError


def _planner(window: int | None, batch: int, cap: int | None = None) -> _Planner:
    settings = ModelSettings("plan", "plan/x", "0" * 40, batch, window, cap)
    return _Planner(settings=settings, cache=None, cache_id="plan/x")


def test_masked_batches_window_and_batch_every_residue_once() -> None:
    batches = _planner(window=8, batch=3).masked_batches(n_tokens=12, residues=[0, 4, 9])
    assert [len(b) for b in batches] == [3]
    first, middle, last = batches[0]
    assert first == MaskedPass(1, Window(0, 8)) and first.local == 1
    assert middle == MaskedPass(5, Window(1, 9)) and middle.local == 4
    assert last == MaskedPass(10, Window(4, 12)) and last.local == 6
    capped = _planner(window=None, batch=16, cap=24).masked_batches(12, range(10))
    assert [len(b) for b in capped] == [2, 2, 2, 2, 2]  # 24 tokens per call / 12 per pass


def _score_set(root: Path, label: str, scores: dict[str, list[float]]) -> None:
    """A score set as `score zero-shot` writes it: ``<label>/assay_id=<id>/data.parquet``."""
    mutants = {"A": ["M1A", "K2C", "V3G", "A4C"], "B": ["M1A", "R5W"]}
    for assay_id, values in scores.items():
        part = root / label / f"assay_id={assay_id}"
        part.mkdir(parents=True)
        frame = pd.DataFrame({"mutant": mutants[assay_id], "model": label, "score": values})
        frame.to_parquet(part / "data.parquet")


def test_cli_check_scores_gates_the_served_model_on_its_pytorch_scores(
    processed_root: Path, tmp_path: Path
) -> None:
    """Agreeing scores pass and are written; reversed ones fail with exit 1."""
    pytorch = {"A": [-3.0, -1.0, 0.5, 2.0], "B": [1.0, -1.0]}
    _score_set(tmp_path / "scores", "esm2_35m_wt", pytorch)
    close = {k: [v + 1e-3 for v in values] for k, values in pytorch.items()}
    _score_set(tmp_path / "scores", "esm2_35m_int8_wt", close)
    command = [
        "onnx", "check-scores", "--config-dir", str(CONF), "--fast",
        "--scores-dir", str(tmp_path / "scores"),
        "--variants-dir", str(processed_root / "processed" / "variants"),
        "--reference", str(processed_root / "DMS_substitutions.csv"),
        "onnx.scorers=[esm2_35m_int8]", f"onnx.results_dir={tmp_path / 'results'}",
        f"tracking.dir={tmp_path / 'mlruns'}",
    ]  # fmt: skip
    passed = CliRunner().invoke(app, command)
    assert passed.exit_code == 0, passed.output
    assert "esm2_35m_int8_wt vs esm2_35m_wt: 2 assays" in passed.output
    table = pd.read_parquet(tmp_path / "results" / "score_parity_esm2_35m_int8_wt.parquet")
    assert table["passed"].all() and table["spearman"].to_numpy() == pytest.approx([1.0, 1.0])

    reversed_a = {"A": pytorch["A"][::-1], "B": close["B"]}
    shutil.rmtree(tmp_path / "scores" / "esm2_35m_int8_wt")
    _score_set(tmp_path / "scores", "esm2_35m_int8_wt", reversed_a)
    failed = CliRunner().invoke(app, command)
    assert failed.exit_code == 1 and "disagree with PyTorch" in failed.output
