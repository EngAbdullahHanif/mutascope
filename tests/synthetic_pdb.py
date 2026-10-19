"""Synthetic AF2-style PDB text for tests: one chain, explicit hydrogens, pLDDT in the B-factor."""

from collections.abc import Sequence

ONE_TO_THREE = {
    "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
    "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
    "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
    "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
}  # fmt: skip


def atom_line(
    serial: int, name: str, resname: str, chain: str, number: int, xyz: Sequence[float], b: float
) -> str:
    """One fixed-column ``ATOM`` record, as AF2 writes them."""
    padded = f" {name:<3}" if len(name) < 4 else name
    x, y, z = xyz
    return (
        f"ATOM  {serial:5d} {padded:<4} {resname:>3} {chain}{number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{b:6.2f}           {name[0]}  "
    )


def make_pdb(
    sequence: str, plddt: Sequence[float], *, first_number: int = 1, chain: str = "A"
) -> str:
    """Backbone plus a CB (not GLY) and an H per residue, an OXT at the end, then TER / END.

    Atom ``k`` of residue ``i`` sits at ``(i + 0.1 * k, 2 * i + 0.25, -i - 0.5)``, so a test
    can predict every coordinate.
    """
    lines: list[str] = []
    serial = 1
    for i, (aa, b) in enumerate(zip(sequence, plddt, strict=True)):
        resname = ONE_TO_THREE[aa]
        names = ["N", "H", "CA", "C", "O"] + ([] if aa == "G" else ["CB"])
        for name in names:
            k = ["N", "CA", "C", "O", "H", "CB"].index(name)
            xyz = (i + 0.1 * k, 2 * i + 0.25, -i - 0.5)
            lines.append(atom_line(serial, name, resname, chain, first_number + i, xyz, b))
            serial += 1
    last = len(sequence) - 1
    lines.append(
        atom_line(
            serial,
            "OXT",
            ONE_TO_THREE[sequence[-1]],
            chain,
            first_number + last,
            (0.0, 0.0, 0.0),
            plddt[-1],
        )
    )
    return "\n".join([*lines, "TER", "END", ""])


def backbone_xyz(i: int, k: int) -> tuple[float, float, float]:
    """Where :func:`make_pdb` puts backbone atom ``k`` (N, CA, C, O) of residue ``i``."""
    return (i + 0.1 * k, 2 * i + 0.25, -i - 0.5)
