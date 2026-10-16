"""Synthetic assays with ProteinGym-style folds, for the fold-join, CV and canary tests.

Each variant's ``DMS_score`` is a substitution effect (wild-type and mutant residue) plus a
per-position effect plus noise, so a model on one-hot residues has something to learn in every
scheme. Folds follow the rules observed in all 217 ProteinGym fold files (ADR 0019): random
per variant; modulo = rank of the position among the assay's mutated positions, mod 5;
contiguous = five ordered blocks of positions.
"""

import csv
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from mutascope.alphabet import AA_ORDER
from mutascope.data.schemas import ProcessedVariantSchema
from mutascope.utils.io import write_partition


def synthetic_assay(
    assay_id: str,
    *,
    length: int = 60,
    per_position: int = 8,
    noise: float = 0.5,
    seed: int = 0,
) -> pd.DataFrame:
    """Variants (valid under :class:`ProcessedVariantSchema`) plus ``fold_<scheme>`` columns."""
    rng = np.random.default_rng(seed)
    target = "".join(rng.choice(list(AA_ORDER), size=length))
    to_effect = dict(zip(AA_ORDER, rng.normal(size=20), strict=True))
    from_effect = dict(zip(AA_ORDER, rng.normal(scale=0.5, size=20), strict=True))
    site = rng.normal(scale=0.7, size=length)
    rows: list[dict[str, object]] = []
    for position, wt in enumerate(target):
        others = [aa for aa in AA_ORDER if aa != wt]
        for mt in rng.choice(others, size=per_position, replace=False):
            score = to_effect[mt] + from_effect[wt] + site[position] + rng.normal(scale=noise)
            rows.append(
                {
                    "assay_id": assay_id,
                    "mutant": f"{wt}{position + 1}{mt}",
                    "position": position,
                    "wt_aa": wt,
                    "mt_aa": str(mt),
                    "DMS_score": float(score),
                }
            )
    variants = pd.DataFrame(rows)
    variants["DMS_score_bin"] = (variants["DMS_score"] > variants["DMS_score"].median()).astype(
        "int64"
    )
    rank = variants["position"].rank(method="dense").astype("int64") - 1
    variants["fold_random"] = rng.integers(0, 5, size=len(variants))
    variants["fold_modulo"] = rank % 5
    variants["fold_contiguous"] = (rank * 5 // (rank.max() + 1)).astype("int64")
    return variants


VARIANT_COLUMNS = ["assay_id", "mutant", "position", "wt_aa", "mt_aa", "DMS_score", "DMS_score_bin"]


def write_variants(assays: list[pd.DataFrame], variants_dir: Path) -> Path:
    """Write the processed-variant partitions of ``assays`` as ``mutascope data clean`` does."""
    variants_dir.mkdir(parents=True)
    with duckdb.connect() as con:
        for assay in assays:
            table = ProcessedVariantSchema.validate(assay[VARIANT_COLUMNS])
            write_partition(con, table, variants_dir / f"assay_id={assay['assay_id'].iat[0]}")
    return variants_dir


def write_fold_csvs(assays: list[pd.DataFrame], folds_dir: Path) -> Path:
    """Write ProteinGym-style ``<assay_id>.csv`` fold files (their column names, extra columns)."""
    folds_dir.mkdir(parents=True)
    for assay in assays:
        path = folds_dir / f"{assay['assay_id'].iat[0]}.csv"
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    *("mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"),
                    *("fold_random_5", "fold_modulo_5", "fold_contiguous_5"),
                ]
            )
            for row in assay.itertuples(index=False):
                writer.writerow(
                    [
                        *(row.mutant, "SEQ", row.DMS_score, row.DMS_score_bin),
                        *(row.fold_random, row.fold_modulo, row.fold_contiguous),
                    ]
                )
    return folds_dir


def write_feature_store(
    assays: list[pd.DataFrame], tmp_dir: Path, *, with_structure: list[str]
) -> Path:
    """A feature store (``features build``) for ``assays``, with real layout and fake models.

    Two models, named as in ``conf/features/default.yaml`` so its sets apply: ``esm2_650m`` on
    every assay and ``saprot_650m_af2`` on ``with_structure`` only, which also get structure
    features. Each measured substitution's log-probability is its ``DMS_score`` plus noise
    (the wild type's is 0), so the zero-shot score carries signal; every other number is
    random. Embeddings are 4 wide.
    """
    from mutascope.eval.landscapes import LandscapeTarget
    from mutascope.eval.region import ScoringRegion
    from mutascope.features.store import ModelSource, build_store
    from mutascope.scoring.cache import CacheKey, LogProbCache, embedding_method

    rng = np.random.default_rng(0)
    log_probs = LogProbCache(tmp_dir / "logprobs")
    embeddings = LogProbCache(tmp_dir / "embeddings", width=None)
    targets = {
        str(a["assay_id"].iat[0]): "".join(a.groupby("position")["wt_aa"].first()) for a in assays
    }
    sources: list[ModelSource] = []
    for name, covered in (("esm2_650m", list(targets)), ("saprot_650m_af2", with_structure)):
        by_assay: dict[str, list[LandscapeTarget]] = {}
        for assay in assays:
            assay_id = str(assay["assay_id"].iat[0])
            if assay_id not in covered:
                continue
            seq = targets[assay_id]
            region = ScoringRegion(assay_id, 0, len(seq), len(seq))
            by_assay[assay_id] = [LandscapeTarget(assay_id, region, seq, None, benchmark=True)]
            lp = rng.normal(size=(len(seq), 20)).astype(np.float32)
            for pos, wt in enumerate(seq):
                lp[pos, AA_ORDER.index(wt)] = 0.0
            for position, mt, score in zip(
                assay["position"].to_numpy(np.int64),
                assay["mt_aa"].astype(str),
                assay["DMS_score"].to_numpy(np.float64),
                strict=True,
            ):
                lp[position, AA_ORDER.index(mt)] = score + rng.normal()
            key = CacheKey(f"hub/{name}", "r" * 40, seq, None, "masked", "full")
            log_probs.put(key, lp)
            emb_key = CacheKey(f"hub/{name}", "r" * 40, seq, None, embedding_method(4), "full")
            embeddings.put(emb_key, rng.normal(size=(len(seq), 4)).astype(np.float32))
        sources.append(ModelSource(name, f"hub/{name}", "r" * 40, by_assay, embeddings, 4))
    structures = {
        assay_id: pd.DataFrame(
            {
                "position": np.arange(len(targets[assay_id])),
                "residue": list(targets[assay_id]),
                "plddt": rng.uniform(50, 100, len(targets[assay_id])),
                "rel_sasa": rng.uniform(0, 1, len(targets[assay_id])),
                "contact_count": rng.integers(0, 20, len(targets[assay_id])),
            }
        )
        for assay_id in with_structure
    }
    reference = pd.DataFrame({"DMS_id": list(targets), "target_seq": list(targets.values())})
    keys = pd.concat(assays)[["assay_id", "position", "wt_aa", "mt_aa"]]
    out = tmp_dir / "features"
    build_store(reference, sources, log_probs, structures, keys, out)
    return out
