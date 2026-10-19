# 0039 — Structure view: backbone files in bundle 1.1.0, a lazy 3Dmol.js viewer linked to the heatmap

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0003 (structure mapping, 213 assays), ADR 0006 (structure features, pLDDT),
  ADR 0028 (bundle format, budgets, publishing), ADR 0030 (pinned by commit), ADR 0037 (site
  reads only the bundle), ADR 0038 (heatmap, summary bar, neutral point)
- Amends: ADR 0028 (a fourth file kind; `budget.structure_bytes`)

## Context

Phase 5 Task 3 links the heatmap to the protein's 3D structure. Bundle 1.0.0 carried
per-residue structure *features* (pLDDT, relative SASA, contacts) but no coordinates, and the
site must work from the bundle alone with the API asleep (ADR 0037). The structures are
ProteinGym's AF2 models matched to the DMS target sequences (data rule 6): 197 PDBs in
`ProteinGym_AF2_structures.zip`, used by the 213 assays with a structure (ADR 0003). Three of
them model only part of the target: `A0A140D2T1_ZIKV_Sourisseau_2019` (291–794 of 3,423),
`BRCA2_HUMAN_Erwood_2022_HEK293T` (1–2,832 of 3,418) and `POLG_HCVJF_Qi_2014`
(1,982–2,225 of 3,033).

## Decision

### 1. One structure file per PDB, backbone only (format 1.1.0)

`mutascope export bundle` writes `structures/<pdb>.json.gz`, a `StructureFile` per PDB used by
an assay with a structure: the PDB's residue numbers, one-letter sequence, N/CA/C/O coordinates
(3 decimals, as in the PDB, so exact) and pLDDT (the B-factor, 2 decimals, exact), plus the
source file's sha256. Side chains, hydrogens and OXT are dropped: the viewer draws a cartoon,
which needs only the backbone. Assays of one protein share the file (185 files for 213 assays).

Each protein file gains `structure_file` (`StructureRef`): path, bytes, sha256, the 0-indexed
target range `start..end` it models (from `pdb_range`, through `StructureMapping.offset`, the one
place that conversion happens) and `low_plddt_threshold` (`structure.low_plddt_threshold`, 70).
It is present exactly where `structure` (the features) is; the model validator enforces it.

A reader can notice the change, so `SCHEMA_VERSION` is 1.1.0 (minor: a new optional file kind
and field), with `schema/structure.schema.json`. The new bundle is tag `bundle-v1.1.0`,
published 2026-10-19 as commit `1bfc294371d4adeb31d8613989bed9055f03409a` (the upload landed as two
commits; the tag and the pin are the second), public, its 410 files identical to the build.

### 2. Every structure residue is checked against its assay's target (data rule 3)

PDBs are read straight from the zip after its sha256 matches `data/manifest.yaml`, never from the
extracted folder, which nothing verifies. For every assay, not once per PDB:

- the backbone parses as one chain, no alternate locations, canonical residues, N/CA/C/O each
  exactly once, residue numbers strictly increasing, one B-factor per residue, 0–100;
- structure residue `i` equals `target_seq[start + i]` for every `i`, and the structure has
  exactly `end - start` residues. A mismatch fails the build naming the assay, the structure
  index, the PDB number, the target position and how many residues differ;
- its pLDDT equals the published structure features (computed from the same PDBs by biotite in
  Phase 1) to 2 decimals, residue by residue: two independent parsers agree on what they read.

All 213 assays passed on the real data. The browser checks again that the file's sequence is
`target_seq[start:end]` (`loadStructure`).

### 3. Budgets: structure files are not protein bytes

Structure files do **not** count towards `export.budget.protein_bytes`: that budget is what the
browser downloads before it can draw the heatmap, and a structure is fetched separately, after
the heatmap. They have their own per-file budget, `structure_bytes` (500,000), and **do** count
towards `total_bytes`, which is what the Hub folder and a static deployment hold.

Measured (2026-10-19): 185 structure files, 2,587,538 bytes, largest
`structures/BRCA2_HUMAN.json.gz` 114,664 bytes (2,832 residues; a quarter of the budget); 217
protein files 29,249,984 bytes (largest 1,154,127, ZIKV); total 31,837,522 of 40,000,000. The
fidelity checks are unchanged and give the same largest differences as 1.0.0 (ADR 0028:
Spearman 4.30e-4 ESM-2, 6.56e-4 SaProt, 4.04e-4 ridge; coverage exact).

### 4. The API never loads structure files

The API serves no structure. `api.app.bundle` lists `structures/` as `IGNORED`: the Hub snapshot
excludes it (`ignore_patterns`), `verify_checksums` skips its lines, and nothing opens a structure
file, so the image and the 4 GiB instance hold none of them. A test lists structure lines with
wrong digests and missing files and the bundle still opens; another checks the download pattern.
The API's responses gain no structure field (`structure_file` is not in `ProteinDetail`); the
only change to `api-types.ts` is the `schema_version` constant of `/v1/benchmarks`, which returns
the bundle's `Benchmarks` model (1.0.0 → 1.1.0).

### 5. Viewer: 3Dmol.js, after the heatmap

3Dmol.js 2.5.5 (BSD-3-Clause, maintained, a plain JS API with TypeScript types, named in
CLAUDE.md's stack). The structure section mounts only after the
heatmap's first frame; the viewer module and 3Dmol.js are separate chunks (`next/dynamic`, then
`import("3dmol")`) and the structure file is fetched only then, through `loadStructure` (protein
file's sha256, schema version, Zod, sequence check). Measured on the production build: heatmap
drawn about 150 ms after navigation, then the viewer chunk (7 kB) and 3Dmol.js (573 kB, 161 kB
gzip) requested, structure file at about 460 ms. The heatmap's 300 ms budget test still passes.

The browser turns the JSON back into PDB text (`toPdb`) so 3Dmol.js assigns bonds and secondary
structure itself from the backbone.

### 6. Colours: the summary bar's, or pLDDT

- **Mean effect** (default): each residue gets the colour of its bar in the heatmap's summary
  (mean over the substitutions shown), from the same lookup table, centre (0 or the assay median,
  ADR 0038), half-width and clipping, and follows the model and predicted/measured switches. The
  legend is `EffectLegend` with the same props and the view's label. A residue without a mean
  (measured view, unmeasured residue) is the missing grey.
- **pLDDT**: a sequential ColorBrewer Purples ramp (40 → 100, light → dark), tokens
  `--color-plddt-*`. Purple is neither pole of the effect scale nor the teal accent, so it cannot
  be read as an effect. The legend marks the threshold.
- **Low confidence** (pLDDT below the protein file's threshold, 70): drawn as a thin trace instead
  of a cartoon, in both modes, so disordered regions are visibly marked whatever the colour.
- **Outside the structure** (the three partial assays): those residues have no 3D position. A
  coverage strip under the viewer shows the whole target in the viewer's colours with them in the
  missing grey, never white, and hovering their heatmap column says the residue is outside the
  structure.

### 7. Linking both ways, through target positions

The page holds one linked residue (0-indexed target position) and which view set it. Heatmap
column hover highlights the residue in 3D (a sphere on its CA); a residue hovered in 3D outlines
its heatmap column and scrolls it into view if needed; a click in 3D centres the heatmap on that
residue, focuses the grid and selects the residue's cell, so the tooltip and live region describe
it. Residue numbers meet positions only in `positionOfResi` / `resiOfPosition`.

Both views expose state as data attributes (`data-highlighted-position`,
`data-hovered-position`, `data-linked-position`, `data-focus-position`). The Playwright tests
assert on those, never on pixels or WebGL output: PIN1 (both directions and click), ZIKV (click
scrolls a 3,423-residue heatmap to a residue inside 291–794), p53 (no structure).

### 8. Accessibility and no structure

The page states that the 3D view cannot be used with a keyboard or screen reader and that the
heatmap and table are the accessible route; the viewer is one `img` with a text label. The four
assays without a structure (ADR 0003) show a message saying why, never an empty viewer.

## Consequences

- New: `mutascope.export.structure`, `StructureFile` / `StructureRef`, `StructureSizeSchema`
  (`results/export/structure_sizes.parquet`), `export.budget.structure_bytes`; web
  `loadStructure`, `src/lib/explorer/structure.ts`, `src/lib/color/sequential.ts`, the structure
  panel, viewer and coverage strip; npm dependency `3dmol` 2.5.5.
- `export bundle` needs the raw AF2 zip, as it already needed the raw reference CSV.
- The API and the site read only 1.1.0; they move to `bundle-v1.1.0` together (one pin, ADR 0030).
- `benchmarks.json` in 1.1.0 also carries the 60 int8 comparison rows added to
  `results/zero_shot/comparisons.parquet` since 1.0.0 (300 rows, was 240; build log 29 expected
  this): content, not format.
- Not done: surfaces, side chains, ligands, multiple models or chains; SaProt is not served live.
