# Remote intake handoff (community dumps)

**Lane overview:** [remote-intake-lanes.md](./remote-intake-lanes.md). **For archivists:**
[archivist-guide.md](./archivist-guide.md).

Contributors upload community dumps (Redump, No-Intro, …) to the Google Drive inbox or the Wasabi
incoming bucket. [**hitsave-intake**](https://github.com/hitsave/hitsave-intake) downloads each
package, **verifies it against its sidecar checksums**, and writes a `ready/` folder. This repo
then runs the preservation pipeline: ClamAV → BagIt → Wasabi `hitsave-dumps` → ledger → Omeka.

**This runs automatically.** The Windmill job `remote_intake_sweep` runs every 30 minutes. For
each package it calls `scripts/remote-intake/run-wasabi-redump-per-folder.sh`, then
`run-gdrive-redump-per-folder.sh`:

1. **Stage** from Drive or Wasabi when `ready/<slug>/game.ingest.yaml` is missing (intake checksum verification).
2. **Ingest** when the ledger row isn't `complete`.
3. **Register in Omeka** (`register-dump-omeka.sh --slug <slug>`) once the ledger row is `complete`. New items start **private**.

Omeka errors are logged, and the ingest status stays unchanged.

## Layout of a ready package

The ingest-worker sees the intake `ready/` tree at `/data/intake/ready` (host: `$HOST_INTAKE/ready`):

```text
/data/intake/ready/{slug}/
  game.ingest.yaml          # ingest-folder.py entrypoint: ingest_kind: dump, source_game_folder
  hitsave-submission.yaml   # catalog and provenance handoff (sidecar fields, Drive/Wasabi source)
  !submissionInfo.txt       # sidecar
  *.iso / *.bin / *.cue …
```

intake sets `source_game_folder` from `handoff.archiver_source_root` in
`hitsave-archiver-config/intake/intake.yaml`. After the package is pruned, only the two YAML files
and a `PRUNED.txt` marker stay. The slug-collision guard reads `hitsave-submission.yaml`
([storage-retention.md](./storage-retention.md)).

## Run steps by hand

Ingest one package. It needs the `container_path_prefix: /data/intake/ready` entry in
`config/preservation/submission-sources.yaml`:

```bash
docker compose run --rm ingest-worker /data/intake/ready/<slug>/game.ingest.yaml
```

Register one package in Omeka. Use `--game-key` instead of `--slug` if you know the ledger key, and
`--force` to patch an item that already exists:

```bash
./scripts/remote-intake/register-dump-omeka.sh --slug <slug>
```

The title comes from `omeka_item_title` in `hitsave-submission.yaml`, falling back to the
`game_key`. The script runs `apply_dump_catalog_omeka.py` in the `omeka-catalog` service against
`$HITSAVE_PRIVATE_CONFIG/omeka-catalog.yaml` (production).

`register-prod-dumps-omeka.sh` reconciles every `complete` row that still has no
`omeka_item_id`.

Before running a step by hand, hold the remote-intake lock or pause the sweep, so the two don't
stage the same package at once ([scripts/remote-intake/README.md](../scripts/remote-intake/README.md)).

## What stays in intake only

- Google Drive and Wasabi API polling. Both lanes wait until every file has been unchanged for `upload_stable_seconds` (1 hour), and skip a package that hasn't changed since it last failed. Both cases exit 3 ("not ready yet"), which the sweep logs but doesn't count as a failure.
- Resumable download.
- **MD5 / SHA-1 / size** checks against the sidecar. A mismatch sends the package to `failed/<slug>/` with `error.txt`, and archiver never sees it. The ledger's Health page lists open intake failures (it mounts `failed/` read-only). A later successful stage removes the entry.

A package whose ledger status is `quarantine` (virus, content policy or manual) is skipped by the sweep; it is never re-ingested automatically.
