# 0002 — ProteinGym data source and checksums

- Status: accepted
- Date: 2026-10-09

## Context

ProteinGym v1.3 is published in two places: the Zenodo record (DOI 10.5281/zenodo.15293562) and
the Marks lab web server (`marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/`), which the
ProteinGym README uses in its download instructions. CLAUDE.md requires a pinned release and
sha256 checks against `data/manifest.yaml`. Upstream publishes only **md5** (on Zenodo), and no
sha256 at all.

## Decision

- **Download from the Marks mirror.** It is the README's documented URL scheme
  (`ProteinGym_{VERSION}/{FILENAME}`), and it serves plain static files with `Content-Length`.
  On 2026-10-09 every file's size there matched Zenodo's to the byte.
- **Trust Zenodo's md5.** Zenodo is the archival, DOI-backed record, and its files cannot change
  after publication. The mirror is just a web server that could change. So each file's expected
  size and md5 are copied from Zenodo into `conf/data/proteingym.yaml`, and the first download from
  the mirror must match them. If the mirror ever serves different bytes, the check fails.
- **Record sha256 on first download, verify it afterwards.** Once the md5 check passes, the
  downloader records the file's sha256 in `data/manifest.yaml`, which is committed (`.gitignore`
  ignores `/data/*` except this file). Every later run re-hashes local files against it and fails
  on any mismatch instead of re-downloading.
- The published leaderboard CSVs come from GitHub with no md5. Their URLs are pinned to the
  `PG_v1.3` tag's commit (`1f8de97`), so their content cannot change, and their sha256 is also
  recorded on first download.

## Consequences

- md5 is not collision-resistant against a deliberate attacker. That is acceptable here: it is
  only used to show that the mirror's copy matches the archived release. Every check after the
  first download uses sha256.
- Anyone who clones the repo gets byte-identical data or a loud failure, with no network access to
  Zenodo needed.
- Adding a file or moving to a new release means adding its Zenodo md5 to the config. Its sha256 is
  then recorded on the first download.
- If the mirror goes away, changing `base_url` to Zenodo's file URLs keeps every check valid,
  because the checksums do not depend on where a file came from.
