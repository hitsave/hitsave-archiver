#!/usr/bin/env bash
# Give remote-intake bags on Wasabi their contributor's original folder name (one-off migration;
# see scripts/rename_bag_payload_folders.py). Holds the remote-intake lock, so it never runs during
# a sweep. Arguments go to the script:
#   rename-bag-payload-folders.sh GAME_KEY ...          plan only
#   rename-bag-payload-folders.sh --execute GAME_KEY    one bag
#   rename-bag-payload-folders.sh --execute --all       every remaining bag
set -euo pipefail
RI="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "$RI/common.sh"
remote_intake_load_host_env
remote_intake_acquire_lock
dir="$(remote_intake_archiver_compose_dir)"
priv="$(remote_intake_private_config_host)"
remote_intake_archiver_compose run --rm -T --no-deps \
  -v "$dir/scripts:/app/scripts:ro" \
  -v "$priv/omeka-catalog.yaml:/config/omeka-catalog.yaml:ro" \
  --entrypoint python ingest-worker /app/scripts/rename_bag_payload_folders.py "$@"
