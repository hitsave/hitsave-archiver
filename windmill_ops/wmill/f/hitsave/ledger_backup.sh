#!/usr/bin/env bash
# Nightly logical backup of the ledger: pg_dump -Fc into HOST_LEDGER_BACKUPS, keep the newest N.
# A copy of the live data dir is not a consistent backup; this dump is (restore: pg_restore).
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
keep="${1:-30}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh
dest="${HOST_LEDGER_BACKUPS:?set HOST_LEDGER_BACKUPS in host.env}"
name="hitsave_ledger-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Runs in a one-off container from the postgres image (pg_dump matches the server version);
# writes to a temp name and renames only after pg_dump succeeds.
archiver_compose run --rm -T --no-deps -v "$dest:/backups" --entrypoint sh postgres -c "
  set -eu
  export PGPASSWORD=\"\$POSTGRES_PASSWORD\"
  pg_dump -h postgres -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc -f /backups/.$name.partial
  pg_restore --list /backups/.$name.partial >/dev/null
  mv /backups/.$name.partial /backups/$name
  ls -1t /backups/hitsave_ledger-*.dump | tail -n +$((keep + 1)) | xargs -r rm -f
  echo \"backup ok: $name (\$(du -h /backups/$name | cut -f1)); kept newest $keep\"
"
