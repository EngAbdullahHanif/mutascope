"""Score every assay's single mutants with a zero-shot model, for the benchmark.

For each assay the scorer is asked only for the positions the assay measured, on the assay's
benchmark scoring region (:mod:`mutascope.eval.region`). The scores are written per assay,
partitioned by ``assay_id`` like every processed table.

Resuming: the scorer's on-disk cache is the checkpoint. It lives outside the output directory
and outside any Nextflow work directory, and an entry is written as soon as its sequence is
done. A run that is killed and started again recomputes nothing that finished before: those
assays are read back from the cache (``positions_computed == 0`` in the run report) and only
the output files are rewritten.
"""

import time
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from mutascope.data.db import connect
from mutascope.eval.region import ScoringRegion, score_assay
from mutascope.eval.schemas import ScoringRunSchema, ZeroShotScoreSchema
from mutascope.scoring.base import Scorer, mutant_positions
from mutascope.utils.io import fresh_tmp_dir, replace_dir, write_partition
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


def score_assays(
    scorer: Scorer,
    reference: pd.DataFrame,
    variants_dir: Path,
    regions: Mapping[str, ScoringRegion],
    out_dir: Path,
    *,
    fast: bool = False,
) -> pd.DataFrame:
    """Score the single mutants of every assay in ``reference`` and write them to ``out_dir``.

    Writes ``out_dir/assay_id=<id>/data.parquet`` (:class:`ZeroShotScoreSchema`). The
    partitions are built in ``<out_dir>.tmp`` and moved into place after the last assay, so
    ``out_dir`` never holds a partial set.

    Args:
        scorer: The model; its cache makes the run resumable (see the module docstring).
        reference: ProteinGym reference table (``DMS_id``, ``target_seq``).
        variants_dir: Processed single mutants, ``assay_id=<id>/data.parquet``.
        regions: Scoring region per assay (:func:`~mutascope.eval.region.scoring_regions`).
        out_dir: Where the score partitions go.
        fast: Wild-type marginal instead of the masked marginal.

    Returns:
        The run report, one row per assay (:class:`ScoringRunSchema`): what this invocation
        scored and how long it took. ``positions_computed`` is 0 for an assay served from
        the cache.

    Raises:
        ValueError: An assay has no variants, or a variant does not fit its scoring region.
    """
    targets = dict(
        zip(reference["DMS_id"].astype(str), reference["target_seq"].astype(str), strict=True)
    )
    tmp = fresh_tmp_dir(out_dir)
    runs: list[dict[str, object]] = []
    started = time.monotonic()
    with connect(variants_dir) as con:
        for index, (assay_id, target_seq) in enumerate(targets.items(), start=1):
            mutants = [
                str(row[0])
                for row in con.execute(
                    "SELECT mutant FROM variants WHERE assay_id = ? ORDER BY mutant", [assay_id]
                ).fetchall()
            ]
            if not mutants:
                raise ValueError(f"{assay_id}: no variants in {variants_dir}")
            region = regions[assay_id]
            positions = mutant_positions(region.sequence(target_seq), mutants, offset=region.start)
            before, assay_started = scorer.positions_computed, time.monotonic()
            scores = score_assay(scorer, target_seq, region, mutants, fast=fast)
            run: dict[str, object] = {
                "assay_id": assay_id,
                "model": scorer.model_id,
                "method": "wt" if fast else "masked",
                "region": region.label,
                "region_length": region.end - region.start,
                "variants": len(mutants),
                "positions": len(positions),
                "positions_computed": scorer.positions_computed - before,
                "seconds": time.monotonic() - assay_started,
            }
            runs.append(run)
            frame = pd.DataFrame(
                {"assay_id": assay_id, "mutant": mutants, "model": scorer.model_id}
            ).assign(score=scores.astype("float64"))
            write_partition(con, ZeroShotScoreSchema.validate(frame), tmp / f"assay_id={assay_id}")
            log.info(
                "assay_scored",
                **run,
                done=f"{index}/{len(targets)}",
                elapsed=round(time.monotonic() - started, 1),
            )
    replace_dir(tmp, out_dir)
    return ScoringRunSchema.validate(pd.DataFrame.from_records(runs), lazy=True)


def load_scores(scores_dir: Path) -> pd.DataFrame:
    """Scores written by :func:`score_assays`: ``assay_id, mutant, model, score``."""
    with connect(scores_dir) as con:
        frame = con.execute(
            "SELECT assay_id, mutant, model, score FROM variants ORDER BY assay_id, mutant"
        ).df()
    return ZeroShotScoreSchema.validate(frame)
