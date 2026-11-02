# hitsave-dump-sidecars

Parsers for disc and cartridge dump sidecars:

- `hitsave_dump_sidecars.redump`: Redump `submissionInfo.txt` (checksums, titles, disc image suffixes)
- `hitsave_dump_sidecars.nointro`: No-Intro / dumping.guide sidecars

Pure standard library, no dependencies. It is the single copy used by both services:

- **hitsave-archiver** installs it from this folder (`ingest-worker/Dockerfile`), for dump
  classification and catalog metadata.
- **hitsave-intake** installs it from GitHub, pinned to a commit in its `requirements.txt`. It
  uses the parser to verify checksums before promoting a package to `ready/`.

## Changing the parser

1. Edit it here and merge to `main` (tests: `tests/test_dump_redump_titles.py`,
   `tests/test_dump_nointro.py`).
2. In hitsave-intake, bump the commit in `requirements.txt` to the merge commit, then rebuild
   the intake image. Until the pin moves, intake keeps the previous version, so a parser change
   never reaches intake by accident.
