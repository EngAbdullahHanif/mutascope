"""ProteinGym's supervised metric and target code, used as a test oracle.

``merge_supervised_metrics`` is the metric computation of ``proteingym/merge_supervised.py`` at
the pinned commit ``1f8de974dead8ff7501eff087b725d14a965e9f9`` (lines computing ``spearman``
and ``mse`` over all out-of-fold rows of one assay and scheme). ``proteinnpt_standardise`` is
the continuous-target branch of ``preprocess_training_targets`` / ``preprocess_test_targets``
in ``proteinnpt/utils/data_utils.py`` (OATML-Markslab/ProteinNPT at
``ba50f15671870039aff8cff97081073dfcc73eb2``). Both MIT licence, Copyright (c) OATML-Markslab.
Reduced to the lines that compute numbers; nothing here is imported by ``src/``.
"""

from typing import Any

import numpy as np
import pandas as pd


def merge_supervised_metrics(normalized_targets: Any, predictions: Any) -> tuple[float, float]:
    all_model_scores = pd.DataFrame(
        {"normalized_targets": normalized_targets, "predictions": predictions}
    )
    spearman = all_model_scores["normalized_targets"].corr(
        all_model_scores["predictions"], method="spearman"
    )
    mse = ((all_model_scores["normalized_targets"] - all_model_scores["predictions"]) ** 2).mean()
    return float(spearman), float(mse)


def proteinnpt_standardise(training_targets: Any, test_targets: Any) -> tuple[Any, Any]:
    mean = float(np.nanmean(training_targets))  # pyright: ignore[reportUnknownArgumentType]
    std = float(np.nanstd(np.array(training_targets)))
    return (training_targets - mean) / std, (test_targets - mean) / std
