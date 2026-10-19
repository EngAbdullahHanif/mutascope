# LinkedIn post

For hiring managers and bioinformatics groups. Under 1,300 characters (checked: see the count
below). Paste the text between the lines.

---

I built MutaScope, a website that predicts the effect of every single amino-acid substitution in a protein and checks them against lab measurements.

It covers ProteinGym's 217 deep mutational scanning assays. Each protein gets a heatmap of every substitution, linked to its AlphaFold structure. You can also score your own sequence live with a small zero-shot model.

What I found:
• Structure helps. SaProt beats ESM-2 650M by 0.046 Spearman (213 assays, paired 95% CI +0.028 to +0.062).
• A ridge model trained per assay beats zero-shot SaProt by 0.092 on positions it never saw, in cross-validation (contiguous split, CI +0.065 to +0.115).
• Its 90% prediction intervals cover 90.8% of held-out measurements.
• Averaging ESM-2 and SaProt was not shown to beat SaProt alone.

These are effects in lab assays, not pathogenicity or clinical predictions.

Under the hood: a Nextflow pipeline, a test that checks my benchmark code against ProteinGym's published leaderboard, a static Next.js site and a FastAPI service on Cloud Run.

Site: https://mutascope.abdullahhanif.me
Code: https://github.com/EngAbdullahHanif/mutascope

I'd love to join a team working on similar ideas, where machine learning meets genomics and biomedicine. If that's you, I'd be glad to hear from you.

---

Length: 1,276 characters, counted with Python's `len` on the text between the lines.

## Numbers and their sources

`benchmarks.json` in `bundle-v1.1.0` (revision `1bfc294`), the rows the Landing and Benchmarks
pages read, rounded as the pages round them.

| In the post | Source row |
| --- | --- |
| 217 assays | `zero_shot_summary`: `esm2_650m`, overall Spearman, `n_assays` 217 |
| +0.046, CI +0.028 to +0.062, 213 assays | `zero_shot_comparisons`: `saprot_650m_af2` − `esm2_650m`, `split` null, overall Spearman |
| +0.092, CI +0.065 to +0.115 | `supervised_comparisons`: `ridge_seq_struct` − `zero_shot_saprot_score`, `split` contiguous, overall Spearman, `size_bin` null |
| 90.8% | `calibration`: contiguous, `mode` scheme, `level` 0.9, `size_bin` all, `statistic` mean (0.9075) |
| Ensemble not shown better than SaProt | `zero_shot_comparisons`: `ensemble_esm2_650m_saprot_650m_af2` − `saprot_650m_af2`: +0.006, CI [−0.002, +0.014], includes 0 |

## Flags: check before posting

- "Beats" for SaProt vs ESM-2 and for ridge vs SaProt: both paired intervals lie above 0, so
  the direction is shown. The size of the gain is the interval, not the single number.
- "Positions it never saw" is what the contiguous split tests (whole blocks of positions are
  held out). The +0.092 is a cross-validation result over 213 assays; the deployed model runs
  on 198 of them (`final_coverage`, `n_assays` 198), so do not quote it as the deployed
  model's accuracy.
- "A test that checks my benchmark code against ProteinGym's published leaderboard" is the
  harness gate: our metrics, run on ProteinGym's own precomputed ESM-2 650M scores, reproduce
  their published average. It checks the evaluation code, not our model.
- "Small zero-shot model" is ESM-2 35M (fast mode: wild-type marginal; precise mode up to 400
  residues). Named in the site's result label, not in the post.
