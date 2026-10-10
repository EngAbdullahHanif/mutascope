"""parse_mutation / parse_mutant: the single 1-indexed -> 0-indexed conversion."""

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import (
    MUTANT_PATTERN,
    MutationParseError,
    Substitution,
    parse_mutant,
    parse_mutation,
)

residues = st.sampled_from(AA_ORDER)
one_based = st.integers(min_value=1, max_value=1_000_000)


@st.composite
def substitutions(draw: st.DrawFn, position: int | None = None) -> tuple[str, int, str]:
    """(wt, 1-based position, mt) with wt != mt."""
    wt = draw(residues)
    mt = draw(residues.filter(lambda aa: aa != wt))
    return wt, draw(one_based) if position is None else position, mt


@given(substitutions())
def test_round_trip_converts_to_zero_based(sub: tuple[str, int, str]) -> None:
    wt, pos, mt = sub
    assert parse_mutation(f"{wt}{pos}{mt}") == Substitution(wt=wt, position=pos - 1, mt=mt)


@given(substitutions())
def test_position_is_never_negative(sub: tuple[str, int, str]) -> None:
    wt, pos, mt = sub
    assert parse_mutation(f"{wt}{pos}{mt}").position >= 0


@given(st.lists(one_based, min_size=1, max_size=8, unique=True).flatmap(
    lambda positions: st.tuples(*(substitutions(p) for p in positions))
))  # fmt: skip
def test_mutant_round_trip_preserves_order(subs: tuple[tuple[str, int, str], ...]) -> None:
    text = ":".join(f"{wt}{pos}{mt}" for wt, pos, mt in subs)
    assert parse_mutant(text) == tuple(Substitution(wt, pos - 1, mt) for wt, pos, mt in subs)
    assert re.fullmatch(MUTANT_PATTERN, text)


@given(st.text())
def test_arbitrary_text_parses_or_raises_parse_error(text: str) -> None:
    try:
        sub = parse_mutation(text)
    except MutationParseError:
        return
    assert f"{sub.wt}{sub.position + 1}{sub.mt}" == text


@given(substitutions(), st.sampled_from("BJOUXZ*-acdefghiklmnpqrstvwy"))
def test_non_canonical_residues_rejected(sub: tuple[str, int, str], bad: str) -> None:
    wt, pos, mt = sub
    for text in (f"{bad}{pos}{mt}", f"{wt}{pos}{bad}"):
        with pytest.raises(MutationParseError):
            parse_mutation(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "A",
        "A12",
        "12G",
        "A0G",  # positions are 1-based
        "A-1G",
        "A012G",  # leading zero
        " A12G",
        "A12G ",
        "A 12G",
        "a12g",
        "A12A",  # synonymous
        "A1.5G",
        "A12G:C13D",  # multi-mutants go through parse_mutant
        "AB12G",
    ],
)
def test_malformed_single_rejected(text: str) -> None:
    with pytest.raises(MutationParseError):
        parse_mutation(text)


@pytest.mark.parametrize("text", ["", ":", "A12G:", ":A12G", "A12G::C13D", "A12G:C12D", "A1C:X2C"])
def test_malformed_mutant_rejected(text: str) -> None:
    with pytest.raises(MutationParseError):
        parse_mutant(text)


def test_error_quotes_original_text() -> None:
    with pytest.raises(MutationParseError, match="'A12A'"):
        parse_mutation("A12A")
