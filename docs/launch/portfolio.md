# Portfolio description

Two lengths: a card line and a short paragraph. First person, no numbers in the card line.

## Card (one line)

MutaScope: predicted effects of every single amino-acid substitution in 217 ProteinGym assays, with an interactive Explorer and an honest benchmark.

## Paragraph

I built MutaScope to see how well protein language models predict the effect of a mutation, and
to show it in a way people can explore. For each of ProteinGym's 217 deep mutational scanning
assays, the site shows every single substitution as a heatmap linked to the AlphaFold
structure, and any sequence can be scored live with ESM-2 35M. Adding structure helps
(SaProt beats ESM-2 650M by 0.046 Spearman over 213 assays), and a ridge model trained per
assay beats zero-shot SaProt by 0.092 on positions held out in blocks, with 90% intervals that
cover 90.8% of held-out measurements. Every comparison is a paired bootstrap. It runs on a
Nextflow pipeline in Python, a static Next.js site and a FastAPI service on Cloud Run. The
results describe effects in lab assays, not pathogenicity.

Links: https://mutascope.abdullahhanif.me · https://github.com/EngAbdullahHanif/mutascope

## Numbers and their sources

Same rows as the LinkedIn post (`docs/launch/linkedin-post.md`), from `benchmarks.json` in
`bundle-v1.1.0`:

| Number | Source row |
| --- | --- |
| 217 assays | `zero_shot_summary`: `esm2_650m`, overall Spearman, `n_assays` 217 |
| +0.046, 213 assays | `zero_shot_comparisons`: `saprot_650m_af2` − `esm2_650m`, overall Spearman |
| +0.092 | `supervised_comparisons`: `ridge_seq_struct` − `zero_shot_saprot_score`, contiguous, overall Spearman |
| 90.8% | `calibration`: contiguous, `mode` scheme, `level` 0.9, `size_bin` all, mean |

## Flags

- "To see how well protein language models predict…" is my guess at your motivation. Replace
  it with your own reason.
- "Positions held out in blocks" is the contiguous split, under cross-validation over 213
  assays; the deployed model covers 198 of them.
