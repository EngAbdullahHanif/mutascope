"""Which slice of a long token sequence a model sees, for models limited to a fixed window.

ESM-2 was trained on at most 1024 tokens (1022 residues plus ``<cls>`` and ``<eos>``). Positions
here are *token* indices into the full tokenised sequence, special tokens included.

- :func:`optimal_window` is ProteinGym's ``get_optimal_window``
  (``proteingym/utils/scoring_utils.py`` at the pinned commit), used for masked marginals: the
  window is centred on the masked position, clamped at the ends.
- :func:`tiled_windows` is for the single-pass wild-type marginal, where one forward pass per
  position would defeat its purpose: half-overlapping tiles, each position read from the tile
  whose centre is nearest (CLAUDE.md, "most central"). ProteinGym has no direct equivalent: its
  default runs a long sequence through the model unwindowed, and its ``overlapping`` option
  averages tiles with sigmoid weights. See ADR 0012.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    """Tokens ``[start, end)`` of the full sequence."""

    start: int
    end: int


def optimal_window(position: int, n_tokens: int, window: int) -> Window:
    """ProteinGym's window for scoring token ``position`` of an ``n_tokens``-long sequence.

    The whole sequence if it fits; otherwise ``window`` tokens with ``position`` at the centre
    (offset ``window // 2``), or the first / last ``window`` tokens near either end. Ported
    from ``get_optimal_window(mutation_position_relative, seq_len_wo_special, model_window)``,
    whose second argument, despite its name, includes the two special tokens.
    """
    if not 0 <= position < n_tokens:
        raise ValueError(f"position {position} outside 0..{n_tokens - 1}")
    half = window // 2
    if n_tokens <= window:
        return Window(0, n_tokens)
    if position < half:
        return Window(0, window)
    if position >= n_tokens - half:
        return Window(n_tokens - window, n_tokens)
    return Window(max(0, position - half), min(n_tokens, position + half))


def tiled_windows(n_tokens: int, window: int) -> tuple[list[Window], list[int]]:
    """Half-overlapping tiles covering ``n_tokens``, and the tile each token is read from.

    Tiles start every ``window // 2`` tokens; the last one is aligned to the end. Each token is
    assigned the tile whose centre is nearest (the first, on a tie).

    Returns:
        The tiles, and for each token position the index of its tile.
    """
    if n_tokens <= window:
        return [Window(0, n_tokens)], [0] * n_tokens
    stride = window // 2
    starts = [*range(0, n_tokens - window, stride), n_tokens - window]
    tiles = [Window(s, s + window) for s in starts]
    centres = [s + window // 2 for s in starts]
    owner = [min(range(len(tiles)), key=lambda t: abs(p - centres[t])) for p in range(n_tokens)]
    return tiles, owner
