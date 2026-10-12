"""Window selection for sequences longer than the model's context."""

from hypothesis import given
from hypothesis import strategies as st
from tests.scoring.proteingym_reference import get_optimal_window

from mutascope.scoring.windows import Window, optimal_window, tiled_windows


@st.composite
def positions(draw: st.DrawFn, windows: tuple[int, ...] = (8, 9, 16, 1024)) -> tuple[int, int, int]:
    window = draw(st.sampled_from(windows))
    n_tokens = draw(st.integers(min_value=1, max_value=4 * window))
    return draw(st.integers(min_value=0, max_value=n_tokens - 1)), n_tokens, window


@given(positions())
def test_optimal_window_is_proteingyms(case: tuple[int, int, int]) -> None:
    position, n_tokens, window = case
    start, end = get_optimal_window(position, n_tokens, window)
    assert optimal_window(position, n_tokens, window) == Window(start, end)


@given(positions(windows=(8, 16, 1024)))
def test_optimal_window_contains_the_position_and_fits(case: tuple[int, int, int]) -> None:
    """For even windows (ESM-2's is 1024). With an odd one, upstream's centred window
    ``[p - w // 2, p + w // 2)`` is one token short, and so is ours."""
    position, n_tokens, window = case
    w = optimal_window(position, n_tokens, window)
    assert w.start <= position < w.end
    assert w.end - w.start == min(n_tokens, window)


def test_long_sequence_middle_window_is_centred() -> None:
    """3,000 tokens: position 1,500 sits at offset 512 of [988, 2012)."""
    assert optimal_window(1500, 3000, 1024) == Window(988, 2012)
    assert optimal_window(5, 3000, 1024) == Window(0, 1024)
    assert optimal_window(2990, 3000, 1024) == Window(1976, 3000)


@given(st.integers(min_value=1, max_value=200), st.sampled_from([8, 16, 32]))
def test_tiles_cover_and_each_token_reads_its_most_central_tile(n_tokens: int, window: int) -> None:
    tiles, owner = tiled_windows(n_tokens, window)
    assert len(owner) == n_tokens
    assert all(t.end - t.start == min(n_tokens, window) for t in tiles)
    for position, tile in enumerate(owner):
        chosen = tiles[tile]
        assert chosen.start <= position < chosen.end
        distance = abs(position - (chosen.start + chosen.end) / 2)
        others = [
            abs(position - (t.start + t.end) / 2) for t in tiles if t.start <= position < t.end
        ]
        assert distance <= min(others)


def test_short_sequence_is_one_tile() -> None:
    assert tiled_windows(10, 1024) == ([Window(0, 10)], [0] * 10)
