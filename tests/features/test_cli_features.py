"""``features embed`` then ``features build`` with the real ``esm2_t6_8M`` checkpoint.

On the toy processed data (two assays of one 5-residue target): the store's scores of the
measured variants are the zero-shot benchmark's scores, the embeddings are one pass per
distinct sequence, and a rerun of ``embed`` computes nothing.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.parse import parse_mutation
from mutascope.eval.zero_shot import load_scores
from mutascope.features.schemas import EmbeddingRunSchema, FeatureStoreReportSchema
from mutascope.features.sets import FeatureSet, assemble

CONF = Path(__file__).parents[2] / "conf"


def test_embed_and_build_with_the_real_8m_model(processed_root: Path, tmp_path: Path) -> None:
    table = pd.read_csv(processed_root / "DMS_substitutions.csv")
    table["MSA_start"], table["MSA_end"] = 1, table["target_seq"].str.len()
    reference = tmp_path / "reference.csv"
    table.to_csv(reference, index=False)
    processed = processed_root / "processed"
    overrides = [
        "model=esm2_8m",
        "eval.scoring_region.msa_region_assays=null",
        f"scoring.cache_dir={tmp_path / 'cache'}",
        f"features.embedding_cache_dir={tmp_path / 'embeddings'}",
        f"tracking.dir={tmp_path / 'mlruns'}",
        "features.models=[esm2_8m]",
        "~features.sets",
        "+features.sets={toy:{structure:false,blocks:[onehot,blosum62,'esm2_8m:score',"
        "'esm2_8m:log_probs','esm2_8m:embedding']}}",
    ]
    common = ["--config-dir", str(CONF), "--reference", str(reference)]
    report_dir = tmp_path / "report"

    def run(*args: str) -> str:
        result = CliRunner().invoke(app, [*args, *common, *overrides])
        assert result.exit_code == 0, result.output
        return result.output

    run(
        "score", "zero-shot", "--variants-dir", str(processed / "variants"),
        "--out-dir", str(tmp_path / "scores"), "--report-dir", str(report_dir),
    )  # fmt: skip
    run("score", "landscapes", "--report-dir", str(report_dir))

    embedded = run("features", "embed", "--report-dir", str(report_dir))
    assert "esm2_8m: 2 landscapes of 2 assays, 1 distinct sequences (one pass each), width 320" in (
        embedded
    )
    assert "1 passes over 5 residues" in embedded
    embed_report = EmbeddingRunSchema.validate(
        pd.read_parquet(report_dir / "embedding_run_esm2_8m.parquet")
    )
    assert embed_report["computed"].tolist() == [True, False]
    assert "0 passes over 0 residues" in run("features", "embed", "--report-dir", str(report_dir))

    store = tmp_path / "features"
    built = run(
        "features", "build", "--variants-dir", str(processed / "variants"),
        "--three-di-dir", str(processed / "three_di"),
        "--structure-features-dir", str(processed / "structure_features"),
        "--out-dir", str(store), "--report", str(tmp_path / "feature_store.parquet"),
    )  # fmt: skip
    assert "2 assays, 190 variants (6 measured), 10 residues" in built
    assert "set toy: 2 assays, 190 variants x 382 features" in built
    report = FeatureStoreReportSchema.validate(pd.read_parquet(tmp_path / "feature_store.parquet"))
    assert report["structure"].tolist() == [True, False]

    benchmark = load_scores(tmp_path / "scores" / "esm2_8m")
    feature_set = FeatureSet("toy", ("esm2_8m:score",), structure=False)
    for assay_id, rows in benchmark.groupby("assay_id"):
        subs = [parse_mutation(m) for m in rows["mutant"]]
        keys = pd.DataFrame(
            {
                "position": [s.position for s in subs],
                "wt_aa": [s.wt for s in subs],
                "mt_aa": [s.mt for s in subs],
            }
        )
        stored = assemble(store, feature_set, str(assay_id), keys).features[:, 0]
        np.testing.assert_array_equal(stored, rows["score"].to_numpy(np.float32))
