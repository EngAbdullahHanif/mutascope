"""The canonical amino-acid alphabet, defined once for the whole package.

Landscapes are ``(L, 20)`` arrays whose columns follow ``AA_ORDER``; parsing and validation use the
same string as the set of allowed residues. Import it; never redefine it.
"""

AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
