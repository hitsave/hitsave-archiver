# Storage and retention

Where each copy of an archived package lives, which copies are permanent, and when local
working copies are deleted.

## Copies

| Copy | Location | Role | Kept |
|------|----------|------|------|
| **Preservation copy (AIP)** | Wasabi (`hitsave-dumps`, press/analog buckets), unpacked BagIt | The permanent archive | Permanently |
| Third-party backup | Copied from Wasabi to an outside provider | Disaster recovery | Permanently (planned, not set up yet) |
| Press-material originals | `/tank2/press-material/<game>/` | Source material | For now; not a preservation copy |
| Contributor originals | Wasabi `hitsave-incoming/.../processed/`, Drive `processed/` | Source uploads after ingest | Until the lanes' own cleanup |
| Intake SIP | `<HOST_INTAKE>/ready/<slug>/` on `/tank` | **Temporary** working copy | Until pruned |
| Local bag | `<HOST_OUTPUT>/bag/...` on `/tank` | **Temporary** working copy | Until pruned |

**Local copies on `/tank` are scratch space, not a backup.** It's fine to correct files there
as a step toward uploading to Wasabi. Never treat them as a second copy, and never "refresh"
them from Wasabi just to fix drift: Wasabi is authoritative, and the local copy is deleted soon
anyway. Backups go **from Wasabi** to a third party, never from `/tank`.

## Pruning local working copies

`scripts/prune-local-working-copies.py`, scheduled in Windmill as
`f/hitsave/prune_local_working_copies` (daily 04:30 UTC), deletes local copies only after
proving the Wasabi copy equals the ingest-time bytes. Payload is not downloaded:

1. Only packages with ledger status `complete`, uploaded at least `min_age_days` ago (default
   14). The delay leaves time for manual post-steps that read local files (DVD menu previews,
   rescans, catalog fixes).
2. Every file in the ledger's ingest-time inventory is hashed locally, and its SHA-256 must
   still match.
3. Every Wasabi object's size and S3 ETag must match the local file (`HEAD` only). With step 2,
   this proves Wasabi equals the ingested bytes.
4. The Wasabi payload manifest must list exactly the inventory, each Wasabi tag file must match
   the Wasabi tagmanifest, and there can be no stray payload objects.
5. The latest Wasabi-side fixity run (`wasabi-fixity-check.py`), if any, must not have failed.
6. Every local payload file must be in the inventory. Anything else was never preserved, so the
   package is refused rather than lose it.

On success it deletes the local bag directory and the payload in `ready/<slug>/`. It **keeps**
`hitsave-submission.yaml` and `game.ingest.yaml` and writes `PRUNED.txt`. The slug-collision
guard (`scripts/remote-intake/ready_source.py`) reads the handoff manifest. Without it, a new
package with the same slug would be skipped as "already preserved". Originals under
`/data/press-material` are never touched. Quarantined and failed packages are never touched.

Each verified package gets a `fixity_run` row with detail `wasabi-etag: …`. The job exits
non-zero if any package fails verification, and that package's local copy is kept.

**Dry run by default.** The schedule ships with `execute: "false"`, which reports what would be
deleted and is read-only everywhere. Set `execute` to `"true"` once a dry-run report looks right.
The job holds the shared remote-intake lock, so intake sweeps skip while it runs.

Manual run (dry run, one package):

```bash
source ../hitsave-archiver-config/host.env
docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  -v "$HOST_INTAKE/ready:/data/intake-ready:ro" \
  --entrypoint python ingest-worker -u /app/scripts/prune-local-working-copies.py \
  --game-key <game_key> [--min-age-days 0]
```

### After pruning

Scripts that need local payload refuse a pruned package. They don't fall back to Wasabi:
`rescan-virus-unscanned.py`, `apply_dvd_menu_bag_previews.py`, `fixity-check.py` (local bag),
and `dvd-menu-preview` (reads ISOs from `ready/`). To rework a pruned package, download it from
Wasabi first. Re-ingest isn't possible from a pruned `ready/`: re-stage it from the original
source.
