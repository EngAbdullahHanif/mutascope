"""The website bundle (ADR 0028) on a synthetic two-assay release. No network, no model."""

import gzip
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.eval.region import ScoringRegion
from mutascope.export.assemble import Budget, BundleInputs, write_bundle
from mutascope.export.bundle import (
    MANIFEST_FILE,
    BundleError,
    Encoding,
    SupervisedInfo,
    ZeroShotModel,
    build_protein,
    decoded_grids,
    load_protein,
)
from mutascope.export.fidelity import Tolerances
from mutascope.export.format import (
    MAX_SAFE_INTEGER,
    Benchmarks,
    ProteinFile,
    SupervisedStamp,
    VersionStamp,
    decimals_below,
    decimals_for,
    dequantize,
    json_schemas,
    quantize,
)
from mutascope.export.hub import CHECKSUMS_FILE, checksums

ROOT = Path(__file__).parents[2]
ENCODING = Encoding(
    score_decimals=3,
    dms_min_decimals=3,
    dms_steps_per_sd=1000,
    dms_max_decimals=9,
    plddt_decimals=2,
    rel_sasa_decimals=3,
)
TOLERANCES = Tolerances(spearman=0.001, coverage=0.002)
INFO = SupervisedInfo(model="ridge_seq_struct", levels=(0.8, 0.9))
VERSION = VersionStamp(
    schema_version="1.0.0",
    mutascope_version="0.1.0",
    code_version="abc",
    proteingym_release="v1.3",
    dataset_repo="user/data",
    dataset_revision="0" * 40,
    model_repo="user/model",
    model_revision="1" * 40,
    supervised=SupervisedStamp(
        code_version="abc", config_sha256="c" * 64, manifest_sha256="d" * 64
    ),
)
SUPERVISED, ZERO_SHOT = "SUP", "ZS"
TARGETS = {SUPERVISED: "MKVARLLW", ZERO_SHOT: "MKVA"}
_AA = {aa: i for i, aa in enumerate(AA_ORDER)}


# --- encoding -----------------------------------------------------------------------------------


def test_quantize_round_trips_within_half_a_step_and_keeps_missing_values() -> None:
    values = np.array([1.23456, -0.0004, math.nan, 12.5])
    q = quantize(values, 3)
    assert q.values == [1235, 0, None, 12500]
    back = dequantize(q)
    assert np.isnan(back[2])
    assert np.nanmax(np.abs(back - values)) <= 0.0005


def test_quantize_refuses_values_a_javascript_number_cannot_hold() -> None:
    with pytest.raises(ValueError, match="too large"):
        quantize([MAX_SAFE_INTEGER / 1e2], 3)
    with pytest.raises(ValueError, match="infinite"):
        quantize([math.inf], 3)


def test_decimals_follow_the_spread() -> None:
    # HSP82's SD of 0.016 needs 5 decimals for a step of at most SD / 1000; SD 1 keeps 3.
    assert decimals_for(0.016433, minimum=3, steps_per_sd=1000) == 5
    assert decimals_for(1.0, minimum=3, steps_per_sd=1000) == 3
    assert decimals_for(50.0, minimum=3, steps_per_sd=1000) == 3
    with pytest.raises(ValueError):
        decimals_for(0.0, minimum=3, steps_per_sd=1000)


def test_decimals_below_a_gap() -> None:
    assert decimals_below(0.0015, maximum=9) == 3  # step 0.001 < 0.0015
    assert decimals_below(0.001, maximum=9) == 4  # a step equal to the gap could tie
    assert decimals_below(3e-12, maximum=9) == 9
    assert decimals_below(math.inf, maximum=9) == 0


@given(
    st.lists(
        st.tuples(
            st.floats(-50, 50, allow_nan=False),
            st.floats(-50, 50, allow_nan=False),
            st.floats(0.001, 5, allow_nan=False),
        ),
        min_size=1,
        max_size=40,
    )
)
def test_a_step_below_the_smallest_gap_keeps_every_inclusive_comparison(
    rows: list[tuple[float, float, float]],
) -> None:
    y = np.array([r[0] for r in rows])
    lower = np.array([r[1] for r in rows])
    upper = lower + np.array([r[2] for r in rows])
    gaps = np.abs(np.concatenate([y - lower, y - upper]))
    gaps = gaps[gaps > 0]
    d = decimals_below(float(gaps.min()) if gaps.size else math.inf, maximum=12)
    if d == 12:  # the cap: the fidelity check, not this rule, is the guarantee there
        return
    r = [dequantize(quantize(a, d)) for a in (y, lower, upper)]
    assert (((y >= lower) & (y <= upper)) == ((r[0] >= r[1]) & (r[0] <= r[2]))).all()


# --- synthetic release ---------------------------------------------------------------------------


def _variants(assay_id: str, cells: list[tuple[int, str, float]]) -> pd.DataFrame:
    target = TARGETS[assay_id]
    return pd.DataFrame(
        {
            "assay_id": assay_id,
            "position": [p for p, _, _ in cells],
            "wt_aa": [target[p] for p, _, _ in cells],
            "mt_aa": [m for _, m, _ in cells],
            "DMS_score": [s for _, _, s in cells],
        }
    )


def _measured_cells() -> dict[str, list[tuple[int, str, float]]]:
    rng = np.random.default_rng(0)
    sup = [(p, m, float(rng.normal())) for p in (0, 1, 2) for m in "ACDEFGH"]
    sup = [c for c in sup if c[1] != TARGETS[SUPERVISED][c[0]]] + [(5, "A", 0.37)]
    zs = [(1, "A", 0.5), (2, "G", -1.25), (3, "C", 0.75)]
    return {SUPERVISED: sup, ZERO_SHOT: zs}


def _landscape(variants: pd.DataFrame) -> pd.DataFrame:
    """A FinalLandscapeSchema-shaped landscape: all 19 x L substitutions of SUPERVISED."""
    target = TARGETS[SUPERVISED]
    rng = np.random.default_rng(1)
    measured = {
        (int(p), str(m)): float(s)
        for p, m, s in zip(
            variants["position"].to_numpy(),
            variants["mt_aa"].to_numpy(),
            variants["DMS_score"].to_numpy(),
            strict=True,
        )
    }
    counts = variants.groupby("position").size().to_dict()
    rows: list[dict[str, Any]] = []
    for p, wt in enumerate(target):
        for mt in AA_ORDER:
            if mt == wt:
                continue
            dms = measured.get((p, mt))
            pred = (dms if dms is not None else 0.0) + float(rng.normal(0, 0.3))
            others = counts.get(p, 0) - (dms is not None)
            n = counts.get(p, 0)
            rows.append(
                {
                    "assay_id": SUPERVISED,
                    "position": p,
                    "wt_aa": wt,
                    "mt_aa": mt,
                    "position_status": "measured"
                    if n == 19
                    else ("partially_measured" if n else "unmeasured"),
                    "measured": dms is not None,
                    "dms_score": math.nan if dms is None else dms,
                    "prediction": pred,
                    "prediction_source": "out_of_fold" if dms is not None else "final_model",
                    "calibration_scheme": "random" if others > 0 else "contiguous",
                    "lower_80": pred - 0.4,
                    "upper_80": pred + 0.4,
                    "lower_90": pred - 0.6,
                    "upper_90": pred + 0.6,
                    "structure_imputed": p >= 6,
                    "code_version": "abc",
                    "config_sha256": "c" * 64,
                    "manifest_sha256": "d" * 64,
                }
            )
    return pd.DataFrame(rows)


def _scores(target: str, seed: int) -> np.ndarray:
    scores = np.random.default_rng(seed).normal(-2, 2, (len(target), 20)).astype(np.float32)
    scores[np.arange(len(target)), [_AA[a] for a in target]] = 0.0
    return scores


def _inputs(**changes: Any) -> BundleInputs:
    cells = _measured_cells()
    variants = pd.concat([_variants(a, c) for a, c in cells.items()], ignore_index=True)
    landscapes = _landscape(variants[variants["assay_id"] == SUPERVISED])
    assays = pd.DataFrame(
        {
            "assay_id": [SUPERVISED, ZERO_SHOT],
            "UniProt_ID": ["P1", "P1"],
            "molecule_name": ["Toy", "Toy"],
            "source_organism": ["E. coli", "E. coli"],
            "coarse_selection_type": ["Stability", "Binding"],
            "selection_assay": ["growth", "binding"],
            "target_seq": [TARGETS[SUPERVISED], TARGETS[ZERO_SHOT]],
            "pdb_file": ["SUP.pdb", None],
            "pdb_range": ["1-6", None],
            "has_structure": [True, False],
        }
    )
    structure = pd.DataFrame(
        {
            "assay_id": SUPERVISED,
            "position": range(6),
            "residue": list(TARGETS[SUPERVISED][:6]),
            "plddt": [91.234, 88.0, 75.5, 60.25, 99.9, 70.0],
            "rel_sasa": [0.1234, 0.5, 1.2, 0.0, 0.33333, 0.75],
            "contact_count": [3, 5, 8, 2, 0, 4],
        }
    )
    manifest = pd.DataFrame(
        {
            "assay_id": [SUPERVISED, ZERO_SHOT],
            "eligible": [True, False],
            "reason": [None, "fewer than 300 measured variants (3)"],
            "default_model": ["ridge_seq_struct", "esm2_650m"],
            "has_structure": [True, False],
            "n_measured": [len(cells[SUPERVISED]), len(cells[ZERO_SHOT])],
            "target_length": [8, 4],
            "landscape_rows": [19 * 8, 0],
        }
    )
    esm = ZeroShotModel(
        "esm2_650m",
        "facebook/esm2",
        "rev",
        {a: _scores(t, i) for i, (a, t) in enumerate(TARGETS.items())},
    )
    saprot = ZeroShotModel(
        "saprot_650m_af2", "westlake/saprot", "rev2", {SUPERVISED: _scores(TARGETS[SUPERVISED], 9)}
    )
    reference = assays.rename(columns={"assay_id": "DMS_id"})
    fields: dict[str, Any] = {
        "assays": assays,
        "variants": variants,
        "structure_features": structure,
        "manifest": manifest,
        "landscapes": landscapes,
        "reference": reference,
        "zero_shot": [esm, saprot],
        "msa_regions": {ZERO_SHOT: ScoringRegion(ZERO_SHOT, 1, 3, 4)},
        "reported": {},
    }
    return BundleInputs(**(fields | changes))


BENCHMARKS = Benchmarks(
    version=VERSION,
    deployed_model="ridge_seq_struct",
    zero_shot_comparator="zero_shot_saprot_score",
    zero_shot_summary=[],
    zero_shot_shared=[],
    zero_shot_comparisons=[],
    supervised_summary=[],
    supervised_comparisons=[],
    function_types=[],
    caveats=[],
    calibration=[],
    final_coverage=[],
)


def _write(tmp_path: Path, inputs: BundleInputs | None = None, **budget: int) -> Any:
    sizes = {"protein_bytes": 2_000_000, "total_bytes": 40_000_000} | budget
    return write_bundle(
        inputs or _inputs(),
        out_dir=tmp_path / "bundle",
        benchmarks=BENCHMARKS,
        version=VERSION,
        encoding=ENCODING,
        supervised_info=INFO,
        budget=Budget(**sizes),
        tolerances=TOLERANCES,
        card="# Card\n",
    )


def _build(assay_id: str, inputs: BundleInputs | None = None) -> tuple[ProteinFile, Any]:
    inputs = inputs or _inputs()

    def of(frame: pd.DataFrame) -> pd.DataFrame:
        return frame[frame["assay_id"] == assay_id]

    structure = of(inputs.structure_features)
    supervised = of(inputs.landscapes)
    return build_protein(
        assay={str(k): v for k, v in of(inputs.assays).iloc[0].to_dict().items()},
        variants=of(inputs.variants),
        structure=structure if len(structure) else None,
        manifest_row={str(k): v for k, v in of(inputs.manifest).iloc[0].to_dict().items()},
        supervised=supervised if len(supervised) else None,
        supervised_info=INFO,
        zero_shot=inputs.zero_shot,
        msa_region=inputs.msa_regions.get(assay_id),
        n_assays_for_protein=2,
        encoding=ENCODING,
        version=VERSION,
    )


# --- protein files -------------------------------------------------------------------------------


def test_round_trip_equals_the_source_within_half_a_step(tmp_path: Path) -> None:
    result = _write(tmp_path)
    assert result.failure is None, result.failure
    for assay_id in TARGETS:
        _, source = _build(assay_id)
        data = (tmp_path / "bundle" / "proteins" / f"{assay_id}.json.gz").read_bytes()
        protein = load_protein(data)
        decoded = decoded_grids(protein)
        assert set(decoded) == set(source.grids)
        for name, values in source.grids.items():
            decimals = (
                protein.zero_shot[name.split(".")[1]].scores.decimals
                if name.startswith("zero_shot.")
                else protein.measured.decimals
            )
            assert np.array_equal(np.isnan(values), np.isnan(decoded[name])), name
            error = np.nanmax(np.abs(values - decoded[name]))
            assert error <= 0.5 * 10.0**-decimals + 1e-12, name


def test_grids_index_position_times_20_plus_amino_acid() -> None:
    protein, _ = _build(SUPERVISED)
    values = dequantize(protein.measured)
    # (5, "A", 0.37) is a measured variant; the wild type of a supervised grid is null.
    assert values[5 * 20 + AA_ORDER.index("A")] == pytest.approx(0.37)
    assert protein.supervised is not None
    wt = 5 * 20 + AA_ORDER.index(TARGETS[SUPERVISED][5])
    assert protein.supervised.prediction.values[wt] is None
    assert protein.zero_shot["esm2_650m"].scores.values[wt] == 0


def test_supervised_file_carries_labels_flags_and_structure() -> None:
    protein, _ = _build(SUPERVISED)
    assert protein.default.label == "Supervised (n = 22)"
    assert protein.default.kind == "supervised"
    assert protein.default.reason is None
    assert set(protein.zero_shot) == {"esm2_650m", "saprot_650m_af2"}
    s = protein.supervised
    assert s is not None
    assert s.structure_imputed == [False] * 6 + [True] * 2
    assert s.position_status[0] == "partially_measured" and s.position_status[7] == "unmeasured"
    # A residue's only measured variant is calibrated on contiguous folds, its others on random.
    codes = s.calibration_scheme
    lone = 5 * 20 + AA_ORDER.index("A")
    assert codes.codes[codes.values[lone] or 0] == "contiguous"
    assert codes.codes[codes.values[lone + 1] or 0] == "random"
    assert protein.structure is not None
    assert protein.structure.contact_count == [3, 5, 8, 2, 0, 4, None, None]
    assert dequantize(protein.structure.plddt)[0] == pytest.approx(91.23)
    assert protein.msa_region is None


def test_zero_shot_file_has_its_reason_and_msa_region_and_no_supervised_block() -> None:
    protein, _ = _build(ZERO_SHOT)
    assert protein.default.label == "Zero-shot"
    assert protein.default.reason == "fewer than 300 measured variants (3)"
    assert protein.supervised is None and protein.structure is None
    assert set(protein.zero_shot) == {"esm2_650m"}
    assert protein.msa_region is not None
    assert (protein.msa_region.label, protein.msa_region.start, protein.msa_region.end) == (
        "msa:2-3",
        1,
        3,
    )


def test_a_wild_type_mismatch_fails_loudly_with_assay_and_row() -> None:
    inputs = _inputs()
    variants = inputs.variants.copy()
    variants.loc[0, "wt_aa"] = "W"
    with pytest.raises(BundleError, match=r"SUP: wild type W at position 0 \(row 0\)"):
        _build(SUPERVISED, _inputs(variants=variants))


def test_a_measurement_that_differs_from_the_landscape_fails() -> None:
    inputs = _inputs()
    landscapes = inputs.landscapes.copy()
    row = landscapes.index[landscapes["measured"]][0]
    landscapes.loc[row, "dms_score"] += 1e-9
    with pytest.raises(BundleError, match="measured values differ"):
        _build(SUPERVISED, _inputs(landscapes=landscapes))


def test_a_missing_substitution_fails() -> None:
    inputs = _inputs()
    with pytest.raises(BundleError, match="supervised rows"):
        _build(SUPERVISED, _inputs(landscapes=inputs.landscapes.iloc[1:]))


def test_an_incomplete_zero_shot_landscape_fails() -> None:
    inputs = _inputs()
    scores = _scores(TARGETS[ZERO_SHOT], 0)
    scores[2, 0] = np.nan
    esm = ZeroShotModel("esm2_650m", "facebook/esm2", "rev", {ZERO_SHOT: scores})
    with pytest.raises(BundleError, match="incomplete"):
        _build(ZERO_SHOT, _inputs(zero_shot=[esm, inputs.zero_shot[1]]))


def test_the_default_model_needs_a_landscape() -> None:
    inputs = _inputs()
    manifest = inputs.manifest.copy()
    manifest.loc[1, "default_model"] = "saprot_650m_af2"
    with pytest.raises(BundleError, match="has no landscape here"):
        _build(ZERO_SHOT, _inputs(manifest=manifest))


# --- the bundle ----------------------------------------------------------------------------------


def test_bundle_has_manifest_benchmarks_schemas_readme_and_checksums(tmp_path: Path) -> None:
    result = _write(tmp_path)
    bundle = tmp_path / "bundle"
    manifest = json.loads((bundle / MANIFEST_FILE).read_text())
    assert [(p["assay_id"], p["label"]) for p in manifest["proteins"]] == [
        (SUPERVISED, "Supervised (n = 22)"),
        (ZERO_SHOT, "Zero-shot"),
    ]
    assert manifest["total_bytes"] == sum(p["bytes"] for p in manifest["proteins"])
    assert result.manifest is not None and result.manifest.total_bytes == manifest["total_bytes"]
    for name in json_schemas():
        assert (bundle / "schema" / name).is_file()
    assert json.loads((bundle / "benchmarks.json").read_text())["deployed_model"] == (
        "ridge_seq_struct"
    )
    assert "## Version" in (bundle / "README.md").read_text()
    assert (bundle / CHECKSUMS_FILE).read_text() == checksums(bundle)
    assert set(result.sizes["assay_id"]) == set(TARGETS)


def test_bundle_bytes_do_not_depend_on_when_it_was_built(tmp_path: Path) -> None:
    _write(tmp_path / "a")
    _write(tmp_path / "b")
    assert (tmp_path / "a" / "bundle" / CHECKSUMS_FILE).read_text() == (
        tmp_path / "b" / "bundle" / CHECKSUMS_FILE
    ).read_text()


def test_fidelity_rows_cover_each_assay_and_aggregate(tmp_path: Path) -> None:
    table = _write(tmp_path).fidelity
    assays = table[table["level"] == "assay"]
    assert set(zip(assays["assay_id"], assays["model"], assays["metric"], strict=True)) == {
        (SUPERVISED, "esm2_650m", "spearman"),
        (ZERO_SHOT, "esm2_650m", "spearman"),
        (SUPERVISED, "saprot_650m_af2", "spearman"),
        (SUPERVISED, "ridge_seq_struct", "spearman"),
        (SUPERVISED, "ridge_seq_struct", "coverage_80"),
        (SUPERVISED, "ridge_seq_struct", "coverage_90"),
    }
    assert table["passed"].all()
    coverage = table[table["metric"] == "coverage_80"]
    assert (coverage["diff_source"] == 0).all()


def test_a_reported_value_the_bundle_cannot_reproduce_fails_and_keeps_the_old_bundle(
    tmp_path: Path,
) -> None:
    assert _write(tmp_path).failure is None
    before = (tmp_path / "bundle" / CHECKSUMS_FILE).read_text()
    wrong = _inputs(reported={(SUPERVISED, "ridge_seq_struct", "coverage_80"): 0.5})
    result = _write(tmp_path, wrong)
    assert result.manifest is None
    assert result.failure is not None and "coverage_80" in result.failure
    assert (tmp_path / "bundle" / CHECKSUMS_FILE).read_text() == before
    assert not (tmp_path / "bundle.tmp").exists()


def test_a_protein_over_its_budget_fails_and_is_named(tmp_path: Path) -> None:
    result = _write(tmp_path, protein_bytes=1000)
    assert result.manifest is None
    assert result.failure is not None and "over the 1,000-byte budget: SUP" in result.failure
    assert not (tmp_path / "bundle").exists()


def test_a_bundle_over_its_total_budget_fails(tmp_path: Path) -> None:
    result = _write(tmp_path, total_bytes=1000)
    assert result.failure is not None and "over the 1,000-byte budget" in result.failure


def test_files_are_gzip_json_a_browser_can_parse(tmp_path: Path) -> None:
    _write(tmp_path)
    raw = gzip.decompress((tmp_path / "bundle" / "proteins" / f"{SUPERVISED}.json.gz").read_bytes())
    document = json.loads(raw)
    assert document["schema_version"] == "1.0.0"
    assert all(isinstance(v, int) or v is None for v in document["measured"]["values"])


# --- JSON Schema ---------------------------------------------------------------------------------


def test_committed_json_schemas_match_the_models() -> None:
    """``docs/schema/bundle`` is what the frontend reads; regenerate with ``export schema``."""
    for name, schema in json_schemas().items():
        committed = json.loads((ROOT / "docs" / "schema" / "bundle" / name).read_text())
        assert committed == schema, f"{name} is stale: run `mutascope export schema`"


def test_schema_command_writes_versioned_schemas(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["export", "schema", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    protein = json.loads((tmp_path / "protein.schema.json").read_text())
    assert protein["$id"] == "urn:mutascope:bundle:1.0.0:protein"


# --- the real bundle -----------------------------------------------------------------------------


@pytest.mark.skipif(
    not (ROOT / "build" / "bundle" / MANIFEST_FILE).is_file(), reason="no built bundle"
)
def test_the_built_bundle_loads_and_matches_its_manifest() -> None:
    """Every file of ``export bundle``'s output parses, validates and has its listed sha256."""
    import hashlib

    bundle = ROOT / "build" / "bundle"
    manifest = json.loads((bundle / MANIFEST_FILE).read_text())
    assert len(manifest["proteins"]) == 217
    for entry in manifest["proteins"]:
        data = (bundle / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(data) == entry["bytes"] <= 2_000_000
        protein = load_protein(data)
        assert protein.assay.assay_id == entry["assay_id"]
        assert protein.default.label == entry["label"]
