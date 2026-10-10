"""Parse ProteinGym mutation strings.

ProteinGym writes a substitution as ``<wt><position><mt>`` with a 1-indexed position into the
assay's ``target_seq`` (``H24C``), and a multi-mutant as substitutions joined by ``:``. Everything
inside MutaScope uses 0-indexed positions. :func:`parse_mutation` is the one place that converts
between the two, so an off-by-one can only ever live in one line.
"""

import re
from dataclasses import dataclass

from mutascope.alphabet import AA_ORDER

_AA = f"[{AA_ORDER}]"
SINGLE_MUTATION_PATTERN = rf"{_AA}[1-9][0-9]*{_AA}"
"""Regex for one substitution: canonical residues, a positive position with no leading zeros."""
MUTANT_PATTERN = rf"{SINGLE_MUTATION_PATTERN}(?::{SINGLE_MUTATION_PATTERN})*"
"""Regex for a mutant: one or more substitutions joined by ``:``."""

_SINGLE = re.compile(rf"({_AA})([1-9][0-9]*)({_AA})")


class MutationParseError(ValueError):
    """A mutation string is not a valid substitution."""


@dataclass(frozen=True)
class Substitution:
    """One amino-acid substitution.

    Attributes:
        wt: Wild-type residue (one of ``AA_ORDER``).
        position: **0-indexed** position in the target sequence.
        mt: Mutant residue (one of ``AA_ORDER``, never equal to ``wt``).
    """

    wt: str
    position: int
    mt: str


def parse_mutation(text: str) -> Substitution:
    """Parse one 1-indexed substitution such as ``"A123G"`` into a 0-indexed :class:`Substitution`.

    This is the only 1-indexed -> 0-indexed conversion in the codebase.

    Raises:
        MutationParseError: ``text`` is not exactly ``<wt><position><mt>`` with canonical residues
            and a positive position without leading zeros, or ``wt == mt`` (a synonymous
            "substitution" changes nothing and would score 0 by construction).
    """
    match = _SINGLE.fullmatch(text)
    if match is None:
        raise MutationParseError(f"not a substitution: {text!r}")
    wt, number, mt = match.groups()
    if wt == mt:
        raise MutationParseError(f"wild type equals mutant: {text!r}")
    return Substitution(wt=wt, position=int(number) - 1, mt=mt)


def parse_mutant(text: str) -> tuple[Substitution, ...]:
    """Parse a mutant (one or more substitutions joined by ``:``), in the order written.

    Raises:
        MutationParseError: Any part fails :func:`parse_mutation`, or two parts share a position.
    """
    subs = tuple(parse_mutation(part) for part in text.split(":"))
    positions = [s.position for s in subs]
    if len(set(positions)) != len(positions):
        raise MutationParseError(f"repeated position in mutant: {text!r}")
    return subs
