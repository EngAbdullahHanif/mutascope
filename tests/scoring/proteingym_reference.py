"""ProteinGym's window function, used as a test oracle.

Copied verbatim from ``proteingym/utils/scoring_utils.py`` at the pinned commit
``1f8de974dead8ff7501eff087b725d14a965e9f9`` (MIT licence, Copyright (c) 2023 OATML-Markslab),
apart from typing and formatting. Nothing here is imported by ``src/``.
"""


def get_optimal_window(
    mutation_position_relative: int, seq_len_wo_special: int, model_window: int
) -> list[int]:
    half_model_window = model_window // 2
    if seq_len_wo_special <= model_window:
        return [0, seq_len_wo_special]
    elif mutation_position_relative < half_model_window:
        return [0, model_window]
    elif mutation_position_relative >= seq_len_wo_special - half_model_window:
        return [seq_len_wo_special - model_window, seq_len_wo_special]
    else:
        return [
            max(0, mutation_position_relative - half_model_window),
            min(seq_len_wo_special, mutation_position_relative + half_model_window),
        ]
