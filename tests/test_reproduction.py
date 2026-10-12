"""Reproduction gate (CLAUDE.md, ADR 0010), in two parts that must both pass.

Harness gate: our metrics and aggregation on ProteinGym's precomputed ``ESM2_650M`` scores over
all rows (singles and multi-mutants) reproduce the published average Spearman 0.414
(|Δ| ≤ 0.0005, the published value's rounding). The same check runs for every metric in
``eval.published`` (Spearman, AUC, MCC, NDCG@10%, top-10% recall) and every model in
``eval.precomputed_models`` (``ESM2_650M``, ``SaProt_650M_AF2``). Two diagnostics per metric
sit next to it so a failure says where the bug is: per-assay values against the published
DMS-level file (the metric), and our aggregation applied to the published per-assay values (the
aggregation). They need the downloaded release and are skipped without it, as in CI.

Model gate: on single mutants, our ESM-2 650M scores have Spearman >= 0.99 with ProteinGym's
precomputed scores in every assay, and our aggregate is within ±0.01 of the aggregate the same
harness computes from their scores on the same rows. It scores all 217 assays through the same
function as ``mutascope score zero-shot``, with the configured cache: after ``make pipeline``
every sequence is a cache hit and the test takes about a minute; on an empty cache it runs the
650M model for hours. ADR 0010's gate is the 650M checkpoint; the same check also runs for the
35M and 150M checkpoints against ``ESM2_35M`` and ``ESM2_150M`` (ADR 0014), with the same limits.

SaProt 650M (ADR 0015) is gated the same way on the 213 assays with a structure, minus two
documented exceptions where ProteinGym's ``SaProt_650M_AF2`` scores were made from wrong inputs.
Two replay tests are the evidence for those entries: our scorer, fed the same wrong inputs,
reproduces ProteinGym's scores for them.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from omegaconf import OmegaConf

from mutascope.data.release import load_release
from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.benchmark import assay_metrics_table
from mutascope.eval.gate import GateThresholds, join_scores, model_gate
from mutascope.eval.metrics import spearman
from mutascope.eval.proteingym import (
    PRECOMPUTED_PREFIX,
    load_precomputed_scores,
    published_assay_level,
    published_summary,
)
from mutascope.eval.region import scoring_regions, structure_regions
from mutascope.eval.zero_shot import load_scores, score_assays
from mutascope.scoring.base import Scorer, build_scorer, mutant_positions, score_mutants
from mutascope.scoring.struct_tokens import AssayStructure, load_struct_tokens
from mutascope.utils.config import as_str_dict, load_config

CONF = Path(__file__).parents[1] / "conf"
TOLERANCE = 0.0005
"""Published values have 3 decimals: ours must round to them (ADR 0010)."""
FLOAT_SLACK = 1e-9
"""Room for binary floating point at the exact rounding boundary (e.g. 0.4135 vs 0.414)."""
FUNCTION_TOLERANCE = 2 * TOLERANCE
"""Function-level values (our extra check, not part of ADR 0010's gate) are rounded twice
upstream: each assay to 3 decimals, then the mean. Each rounding moves a value by up to 0.0005.
SaProt Binding Spearman is the case that needs it: 0.37853 unrounded, but exactly 0.3785 from
the rounded per-assay values, which rounds half-to-even to the published 0.378 (ADR 0011)."""

_cfg = load_config(CONF)
_release = load_release(_cfg.data)
SCORES_DIR = _release.raw_dir / _release.file("zero_shot_scores").extract_dirname
REFERENCE = _release.raw_dir / _release.file("reference").filename
PUBLISHED = {
    str(metric): (Path(files.summary), Path(files.assay_level))
    for metric, files in _cfg.eval.published.items()
}
"""metric -> (summary file, DMS-level file)."""
NAMES = {str(k): str(v) for k, v in _cfg.eval.precomputed_models.items()}

needs_release = pytest.mark.skipif(
    not (
        SCORES_DIR.is_dir()
        and REFERENCE.is_file()
        and all(f.is_file() for files in PUBLISHED.values() for f in files)
    ),
    reason="needs the downloaded ProteinGym release (`mutascope data download`)",
)
per_metric = pytest.mark.parametrize("metric", list(PUBLISHED))


@pytest.fixture(scope="module")
def our_metrics() -> pd.DataFrame:
    """Every metric of every precomputed model over all rows: model, assay_id, metric, value."""
    variants = load_precomputed_scores(SCORES_DIR, list(NAMES), "all")
    table = assay_metrics_table(variants, {c: c for c in NAMES}, float(_cfg.eval.top_percent))
    return table[["model", "assay_id", "metric", "value"]]


@pytest.fixture(scope="module")
def reference() -> pd.DataFrame:
    return pd.read_csv(REFERENCE)


def test_gate_covers_all_metrics_and_the_adr_0010_target() -> None:
    """The config lists all five metrics, and the headline target is the one ADR 0010 names."""
    assert set(PUBLISHED) == {"spearman", "auc", "mcc", "ndcg", "top_recall"}
    if PUBLISHED["spearman"][0].is_file():
        published = published_summary(PUBLISHED["spearman"][0], NAMES)
        overall = published[published["level"] == "overall"].set_index("model")["value"]
        assert overall["ESM2_650M"] == 0.414


@needs_release
@per_metric
def test_harness_reproduces_published_leaderboard(
    metric: str, our_metrics: pd.DataFrame, reference: pd.DataFrame
) -> None:
    """The gate: overall within 0.0005 (ADR 0010); function types within 0.001 (two roundings)."""
    published = published_summary(PUBLISHED[metric][0], NAMES)
    overall = published[published["level"] == "overall"].set_index("model")["value"]
    mine = our_metrics[our_metrics["metric"] == metric]
    for model, rows in mine.groupby("model"):
        groups = AssayGroups.from_reference(reference, rows["assay_id"].tolist())
        ours = aggregate(rows["value"].to_numpy(np.float64), groups)
        assert len(rows) == 217
        assert abs(ours.overall - overall[model]) <= TOLERANCE + FLOAT_SLACK, (
            f"{PRECOMPUTED_PREFIX}{model} {metric}: ours {ours.overall:.5f}, "
            f"published {overall[model]}"
        )
        functions = published[(published["model"] == model) & (published["level"] == "function")]
        for function, value in zip(functions["group"], functions["value"], strict=True):
            delta = ours.by_function[str(function)] - value
            assert abs(delta) <= FUNCTION_TOLERANCE + FLOAT_SLACK, (
                f"{model} {metric} {function}: {delta:+.5f}"
            )


@needs_release
@per_metric
def test_per_assay_values_match_published_dms_level(metric: str, our_metrics: pd.DataFrame) -> None:
    """Diagnostic for the metric: every assay rounds to ProteinGym's 3-decimal value."""
    published = published_assay_level(PUBLISHED[metric][1], NAMES)
    mine = our_metrics[our_metrics["metric"] == metric]
    joined = published.merge(mine, on=["model", "assay_id"], suffixes=("_pub", ""))
    assert len(joined) == len(published) == 2 * 217
    gap = (joined["value"] - joined["value_pub"]).abs()
    worst = joined.iloc[int(np.argmax(gap.to_numpy()))]
    assert gap.max() <= TOLERANCE + FLOAT_SLACK, f"worst: {dict(worst)}"


@needs_release
@per_metric
def test_aggregation_of_published_values_is_exact(metric: str, reference: pd.DataFrame) -> None:
    """Diagnostic for the aggregation: on ProteinGym's own (rounded) per-assay values, our
    aggregation gives the published aggregates exactly once rounded the way pandas does
    (half to even after scaling; Python's ``round`` would turn 0.3785 into 0.379, not 0.378)."""
    summary_file, assay_level_file = PUBLISHED[metric]
    per_assay = published_assay_level(assay_level_file, NAMES)
    published = published_summary(summary_file, NAMES)
    for model, rows in per_assay.groupby("model"):
        groups = AssayGroups.from_reference(reference, rows["assay_id"].tolist())
        ours = aggregate(rows["value"].to_numpy(np.float64), groups)
        expected = published[published["model"] == model]
        for level, group, value in zip(
            expected["level"], expected["group"], expected["value"], strict=True
        ):
            mine = ours.overall if level == "overall" else ours.by_function[str(group)]
            assert np.round(mine, 3) == value, (
                f"{model} {metric} {level} {group}: {mine} vs {value}"
            )


VARIANTS_DIR = Path(_cfg.data.processed_dir) / "variants"


GATED_MODELS = {"esm2_650m": "ESM2_650M", "esm2_150m": "ESM2_150M", "esm2_35m": "ESM2_35M"}
"""Our model config -> ProteinGym's precomputed column. The first is ADR 0010's gate."""


@pytest.mark.slow
@needs_release
@pytest.mark.skipif(
    not VARIANTS_DIR.is_dir(), reason="needs the processed variants (`make pipeline`)"
)
@pytest.mark.parametrize("model", list(GATED_MODELS))
def test_esm2_matches_proteingym_scores_on_single_mutants(
    model: str, reference: pd.DataFrame, tmp_path: Path
) -> None:
    """The model gate (ADR 0010), on all 696,311 single mutants of the 217 assays."""
    assert str(_cfg.model.name) == "esm2_650m"  # the default model is the one ADR 0010 gates
    cfg = load_config(CONF, [f"model={model}"])
    column = str(cfg.model.proteingym_column)
    assert column == GATED_MODELS[model]
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model"),
        device=str(cfg.scoring.device),
        cache_dir=Path(cfg.scoring.cache_dir),
    )
    regions = scoring_regions(
        reference, "benchmark", [str(a) for a in cfg.eval.scoring_region.msa_region_assays]
    )
    score_assays(scorer, reference, VARIANTS_DIR, regions, tmp_path / "scores")
    theirs = load_precomputed_scores(SCORES_DIR, [column], "single")
    joined = join_scores(load_scores(tmp_path / "scores"), theirs, column)
    thresholds = GateThresholds(
        float(cfg.eval.model_gate.min_assay_spearman),
        float(cfg.eval.model_gate.aggregate_tolerance),
    )
    assert (thresholds.min_assay_spearman, thresholds.aggregate_tolerance) == (0.99, 0.01)
    gate = model_gate(joined, reference, thresholds)

    assert len(gate.per_assay) == 217 and len(joined) == 696_311
    failing = gate.failing[["assay_id", "spearman", "max_abs_diff"]]
    assert failing.empty, f"assays below 0.99 per-variant Spearman:\n{failing.to_string()}"
    assert abs(gate.aggregate_delta) <= thresholds.aggregate_tolerance, (
        f"aggregate Spearman: ours {gate.ours:.4f}, theirs {gate.theirs:.4f}"
    )


# --- SaProt 650M (ADR 0015) ---------------------------------------------------------------------

SAPROT = "saprot_650m_af2"
SAPROT_COLUMN = "SaProt_650M_AF2"
PROCESSED = Path(_cfg.data.processed_dir)
DMS_DIR = _release.raw_dir / _release.file("dms_substitutions").extract_dirname
HCVJF, BRCA2 = "POLG_HCVJF_Qi_2014", "BRCA2_HUMAN_Erwood_2022_HEK293T"
needs_structures = pytest.mark.skipif(
    not (VARIANTS_DIR.is_dir() and (PROCESSED / "three_di").is_dir() and DMS_DIR.is_dir()),
    reason="needs the processed variants and structure tables (`make pipeline`)",
)


def _saprot(reference: pd.DataFrame) -> tuple[Scorer, dict[str, AssayStructure]]:
    """The configured SaProt scorer (with its cache) and every assay's structure tokens."""
    cfg = load_config(CONF, [f"model={SAPROT}"])
    assert str(cfg.model.proteingym_column) == SAPROT_COLUMN
    scorer = build_scorer(
        as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), "model"),
        device=str(cfg.scoring.device),
        cache_dir=Path(cfg.scoring.cache_dir),
    )
    targets = dict(zip(reference["DMS_id"], reference["target_seq"], strict=True))
    structures = load_struct_tokens(
        PROCESSED / "three_di",
        PROCESSED / "structure_features",
        targets,
        float(cfg.model.plddt_threshold),
    )
    return scorer, structures


def _proteingym_saprot(assay_id: str) -> pd.DataFrame:
    rows = load_precomputed_scores(SCORES_DIR, [SAPROT_COLUMN], "single")
    return rows[rows["assay_id"] == assay_id].reset_index(drop=True)


@pytest.mark.slow
@needs_release
@needs_structures
def test_saprot_matches_proteingym_scores_except_two_documented_upstream_errors(
    reference: pd.DataFrame, tmp_path: Path
) -> None:
    """The model gate for SaProt: 213 assays with a structure, 665,793 single mutants; 211
    gated. The two exceptions must really disagree (our correct inputs against ProteinGym's
    scores), or the entry is stale and the gate fails."""
    cfg = load_config(CONF, [f"model={SAPROT}"])
    scorer, structures = _saprot(reference)
    no_structure = {str(a) for a in cfg.data.structure_exceptions}
    assert set(structures) == set(reference["DMS_id"]) - no_structure and len(structures) == 213
    score_assays(
        scorer,
        reference,
        VARIANTS_DIR,
        structure_regions(reference, structures),
        tmp_path / "scores",
        structures=structures,
    )
    theirs = load_precomputed_scores(SCORES_DIR, [SAPROT_COLUMN], "single")
    theirs = theirs[theirs["assay_id"].isin(structures)]
    joined = join_scores(load_scores(tmp_path / "scores"), theirs, SAPROT_COLUMN)
    thresholds = GateThresholds(
        float(cfg.eval.model_gate.min_assay_spearman),
        float(cfg.eval.model_gate.aggregate_tolerance),
    )
    assert (thresholds.min_assay_spearman, thresholds.aggregate_tolerance) == (0.99, 0.01)
    exceptions = [str(a) for a in cfg.eval.model_gate.exceptions[SAPROT]]
    assert sorted(exceptions) == [BRCA2, HCVJF]
    gate = model_gate(joined, reference, thresholds, exceptions)

    assert len(gate.per_assay) == 213 and len(joined) == 665_793
    assert int((~gate.per_assay["exception"]).sum()) == 211
    failing = gate.failing[["assay_id", "spearman", "max_abs_diff"]]
    assert failing.empty, f"assays below 0.99 per-variant Spearman:\n{failing.to_string()}"
    assert gate.stale.empty, f"exceptions that now agree: {gate.stale['assay_id'].tolist()}"
    assert abs(gate.aggregate_delta) <= thresholds.aggregate_tolerance, (
        f"aggregate Spearman: ours {gate.ours:.4f}, theirs {gate.theirs:.4f}"
    )
    # (a) With correct inputs the two exceptions are nowhere near ProteinGym's scores.
    assert gate.exceptions["spearman"].max() < 0.5
    assert gate.passed


@pytest.mark.slow
@needs_release
@needs_structures
def test_replaying_the_shifted_structure_range_reproduces_proteingym_hcvjf_scores(
    reference: pd.DataFrame,
) -> None:
    """(b) ``POLG_HCVJF_Qi_2014``. The reference file in use when ProteinGym's SaProt scores
    were made had ``pdb_range`` 1981-2224; the structure is of residues 1982-2225 (v1.3). Their
    script zips ``target_seq[1980:2224]`` with the structure's 244 tokens, so each residue got
    the token of the next one. The same input reproduces their scores."""
    scorer, structures = _saprot(reference)
    target = str(reference.set_index("DMS_id").loc[HCVJF, "target_seq"])
    structure = structures[HCVJF]
    assert (structure.start, structure.end) == (1981, 2225)  # 1982-2225, 1-based
    start = structure.start - 1
    seq = target[start : structure.end - 1]
    shifted_tokens = structure.tokens[structure.start : structure.end]
    theirs = _proteingym_saprot(HCVJF)
    mutants = theirs["mutant"].tolist()
    landscape = scorer.landscape(
        seq,
        shifted_tokens,
        region="upstream-error:1981-2224",
        positions=mutant_positions(seq, mutants, offset=start),
    )
    replay = score_mutants(landscape, seq, mutants, offset=start).astype(np.float64)
    their_scores = theirs[SAPROT_COLUMN].to_numpy(np.float64)
    assert len(theirs) == 1630
    assert spearman(their_scores, replay) > 0.9999
    assert np.abs(replay - their_scores).max() < 1e-3


@pytest.mark.slow
@needs_release
@needs_structures
def test_replaying_chunks_and_row_misassignment_reproduces_proteingym_brca2_scores(
    reference: pd.DataFrame,
) -> None:
    """(b) ``BRCA2_HUMAN_Erwood_2022_HEK293T``. The old reference file listed three structure
    chunks. Their script scores the rows of each chunk in turn, concatenates the three score
    arrays and assigns the result to the table in file order. The file is not sorted by
    position, so each row received the score of another variant. Scoring per chunk and
    assigning the same way reproduces their scores; assigning correctly does not."""
    scorer, structures = _saprot(reference)
    target = str(reference.set_index("DMS_id").loc[BRCA2, "target_seq"])
    tokens = structures[BRCA2].tokens
    rows = pd.read_csv(DMS_DIR / f"{BRCA2}.csv")["mutant"].astype(str).tolist()  # file order
    assert len(rows) == 265 and not any(":" in m for m in rows)
    position = [int(m[1:-1]) for m in rows]  # 1-based, as upstream filters the rows
    assert position != sorted(position)

    chunk_order: list[str] = []
    score_of: dict[str, float] = {}
    for first, last in ((1, 1000), (1001, 2085), (2086, 2832)):
        mutants = [m for m, p in zip(rows, position, strict=True) if first <= p <= last]
        seq = target[first - 1 : last]
        landscape = scorer.landscape(
            seq,
            tokens[first - 1 : last],
            region=f"upstream-error:chunk-{first}-{last}",
            positions=mutant_positions(seq, mutants, offset=first - 1),
        )
        scores = score_mutants(landscape, seq, mutants, offset=first - 1)
        chunk_order += mutants
        score_of |= dict(zip(mutants, scores.astype(np.float64), strict=True))
    assert len(chunk_order) == len(rows)

    theirs = _proteingym_saprot(BRCA2).set_index("mutant")[SAPROT_COLUMN]
    their_scores = np.array([theirs[m] for m in rows], dtype=np.float64)
    as_assigned_upstream = np.array([score_of[m] for m in chunk_order])  # row i <- chunk_order[i]
    on_the_right_rows = np.array([score_of[m] for m in rows])
    assert sum(a != b for a, b in zip(rows, chunk_order, strict=True)) == 265
    assert spearman(their_scores, as_assigned_upstream) > 0.9999
    assert np.abs(as_assigned_upstream - their_scores).max() < 1e-3
    assert abs(spearman(their_scores, on_the_right_rows)) < 0.2
