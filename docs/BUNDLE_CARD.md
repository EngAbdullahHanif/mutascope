# MutaScope website bundle

Precomputed mutation landscapes for the 217 ProteinGym v1.3 substitution assays, in the format
the MutaScope website reads. Built by `mutascope export bundle` (ADR 0028) from the processed
dataset and supervised landscapes in this repository, the final models in
`engAbdullahHanif/mutascope-ridge-seq-struct` and the zero-shot landscapes of ESM-2 650M and
SaProt 650M (AF2).

## Files

- `manifest.json`: every assay with its default model and label ("Supervised (n = …)" or
  "Zero-shot"), file size and sha256.
- `proteins/<assay_id>.json.gz`: one gzip JSON file per assay: assay metadata, the measured
  `DMS_score`, the zero-shot landscapes, per-residue structure features, the MSA region of the
  five assays benchmarked on part of their target, and, for the 198 assays with a supervised
  model, its predictions with 80% and 90% CV+ intervals.
- `structures/<pdb>.json.gz` (format 1.1.0): one per ProteinGym AF2 structure used by an assay
  (185 files for the 213 assays with a structure): PDB residue numbers, sequence, backbone
  (N, CA, C, O) coordinates in Å and pLDDT. A protein file's `structure_file` names its file,
  sha256 and the target residues it models; every residue was checked against the target.
  AlphaFold2 models from ProteinGym (MIT); cite Jumper et al., *Nature* 2021.
- `benchmarks.json`: the headline zero-shot and supervised tables, paired comparisons, the
  per-function-type table with its caveats, and interval coverage.
- `schema/`: JSON Schemas of the four file kinds; their `$id` carries the format version.
- `SHA256SUMS`: checksums of every file above.

## Reading a protein file

A per-variant field is an `(L, 20)` grid flattened row-major: residue (0-indexed), then amino
acid in the order `ACDEFGHIKLMNPQRSTVWY`. Numbers are fixed-point: `{"decimals": d, "values":
[...]}` means `value / 10**d`, and `null` is no value. Zero-shot scores are
`log P(mt) - log P(wt)` (masked marginal) over the full target, 0 at the wild type. Supervised
predictions are out-of-fold at measured variants and never an in-sample fit.

These are functional effects in deep mutational scanning assays, not clinical or pathogenicity
predictions. Zero-shot and supervised results are labelled as such and never mixed.
