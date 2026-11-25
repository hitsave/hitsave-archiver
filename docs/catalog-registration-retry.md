# Retrying failed Omeka registrations

A package is ingested in two steps: the ingest worker bags it and uploads it to Wasabi, then
`scripts/register-dip-omeka-api.py` creates or updates its Omeka item. The second step talks to
archive.hitsave.org over the internet, so it can fail when the connection drops even though the
bag is safe. A batch notes the failure in its log and moves on, and rerunning the whole batch
to catch up is slow: it starts several containers for every folder in it.

So registration notes its own outcome in the ledger, and a small Windmill job retries the
failures.

## What the ledger records

Schema 016 adds these columns to `game_ingest`:

| Column | Meaning |
|---|---|
| `catalog_error` | The last registration error, with Omeka API keys removed. Empty once registration succeeds. |
| `catalog_attempts` | Failed attempts since the last success. |
| `catalog_attempted_at` | When registration last ran for the package. |
| `catalog_config_path` | The generated game config registration ran with, so the retry job can run it again. |

`register-dip-omeka-api.py` writes them every time it runs for a complete package, whoever
started it: a batch, the remote-intake sweep or a person. It doesn't touch them for a package
that isn't complete yet, or when it fails before it has found the package's ledger row.

The ledger shows the error in the **Problems** panel of the package's page. A package with no
Omeka item at all is also listed on the Health page under "complete, no catalog record".

Registration also locks the package for as long as it runs. A second registration of the same
package, from a batch and the retry job at once for example, skips it instead of creating a
duplicate Omeka item.

## The retry job

`scripts/catalog_registration.py` runs as the Windmill job
`f/hitsave/retry_catalog_registration` at 05:00 and 17:00 UTC. The morning run comes before the
catalog copies and the Glacier copy, so a package it registers gets its catalog record copied
the same morning.

Each run takes up to 50 complete packages that have an error, oldest attempt first, and runs
`register-dip-omeka-api.py` again with the config in `catalog_config_path`. Registration keeps
the Omeka item when the package already has one, so a retry finishes a half-done registration
instead of starting over.

The job leaves a package alone after 10 failed attempts, and skips a package whose config file
is missing. Both need a person, and the job fails while there are any, so they show up in
Windmill. To see what a run would do without changing anything, run the job with `execute`
set to `'false'`.

## Registering a package by hand

Fix the cause first, then run registration with the package's config, from the archiver
checkout on saturn:

```bash
docker compose run --rm -T omeka-catalog /config/preservation/generated/<batch>/<folder>.yaml
```

A success clears the error. If the config is gone, regenerate the batch's configs with
`scripts/batch-expand-preservation.sh <batch.yml>` first.
