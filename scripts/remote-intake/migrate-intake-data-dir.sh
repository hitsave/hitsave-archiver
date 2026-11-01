#!/usr/bin/env bash
# One-off cutover: move the intake data dir from .../gdrive-intake to .../intake and rewrite
# the paths that record it (hitsave-intake rename, phase 2).
#   1. back up game_ingest rows whose source_path uses the old container prefix (CSV);
#   2. mv <old host dir> <new host dir> (same filesystem: instant);
#   3. rewrite source_game_folder in ready/*/game.ingest.yaml;
#   4. UPDATE game_ingest.source_path old prefix -> new prefix (one transaction, count checked).
# Wasabi bags keep the old path in their provenance metadata on purpose (historical record).
# Package identity is unaffected: sip_fingerprint uses the path RELATIVE to the prefix.
#
# Usage: migrate-intake-data-dir.sh [--execute] OLD_HOST_DIR NEW_HOST_DIR
# Dry run by default. Takes the remote-intake lock (or reuses one already held).
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env

execute=0
if [[ "${1:-}" == "--execute" ]]; then execute=1; shift; fi
old_host="${1:?OLD_HOST_DIR (e.g. /tank/hitsave-archiver/gdrive-intake)}"
new_host="${2:?NEW_HOST_DIR (e.g. /tank/hitsave-archiver/intake)}"
old_prefix="/data/gdrive-intake/ready"
new_prefix="/data/intake/ready"

[[ -d "$old_host/ready" ]] || { echo "no $old_host/ready" >&2; exit 1; }
[[ ! -e "$new_host" ]] || { echo "$new_host already exists; refusing" >&2; exit 1; }
[[ "$(stat -c %d "$old_host")" == "$(stat -c %d "$(dirname "$new_host")")" ]] \
  || { echo "different filesystems; refusing (mv would copy)" >&2; exit 1; }

psql_q() { remote_intake_archiver_compose exec -T postgres psql -U hitsave -d hitsave_ledger -v ON_ERROR_STOP=1 -tAc "$1"; }
rows="$(psql_q "SELECT count(*) FROM game_ingest WHERE source_path LIKE '${old_prefix}/%'")"
yamls="$(grep -l "^source_game_folder: ${old_prefix}/" "$old_host"/ready/*/game.ingest.yaml 2>/dev/null | wc -l)"
echo "ledger rows to rewrite: $rows"
echo "game.ingest.yaml files to rewrite: $yamls"
echo "move: $old_host -> $new_host"
if [[ "$execute" != 1 ]]; then echo "dry run; pass --execute"; exit 0; fi

remote_intake_acquire_lock
gen="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$(remote_intake_archiver_root)/.generated}"
backup="$gen/game_ingest-source_path-backup-$(date +%Y%m%dT%H%M%S).csv"
psql_q "COPY (SELECT id, game_key, source_path FROM game_ingest WHERE source_path LIKE '${old_prefix}/%') TO STDOUT WITH CSV HEADER" >"$backup"
echo "backup: $backup ($(($(wc -l <"$backup") - 1)) rows)"

mv "$old_host" "$new_host"
echo "moved."
n=0
for f in "$new_host"/ready/*/game.ingest.yaml; do
  if grep -q "^source_game_folder: ${old_prefix}/" "$f"; then
    sed -i "s#^source_game_folder: ${old_prefix}/#source_game_folder: ${new_prefix}/#" "$f"
    n=$((n + 1))
  fi
done
echo "rewrote $n game.ingest.yaml"

# Single statement = single transaction; RETURNING gives the exact count rewritten.
updated="$(psql_q "WITH u AS (UPDATE game_ingest
    SET source_path = '${new_prefix}/' || substr(source_path, length('${old_prefix}/') + 1)
    WHERE source_path LIKE '${old_prefix}/%' RETURNING 1) SELECT count(*) FROM u")"
left="$(psql_q "SELECT count(*) FROM game_ingest WHERE source_path LIKE '${old_prefix}/%'")"
echo "ledger rows rewritten: $updated (expected $rows); left under old prefix: $left"
[[ "$left" == 0 && "$updated" == "$rows" ]] || { echo "ERROR: ledger rewrite mismatch; restore from $backup" >&2; exit 1; }
