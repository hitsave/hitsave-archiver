# shellcheck shell=bash
# Shared paths for community-dump remote intake (Drive / Wasabi → archiver).
# Source from operator host.env: HITSAVE_PRIVATE_CONFIG, HOST_INTAKE, HOST_OUTPUT.

remote_intake_compat_env() {
  # Compatibility: accept the pre-rename gdrive-intake variable names (hitsave-intake rename,
  # 2026-11). Old names fill in new ones only when the new name is unset.
  for _pair in HOST_INTAKE:HOST_GDRIVE_INTAKE HITSAVE_INTAKE_HOST:HITSAVE_GDRIVE_INTAKE_HOST \
    HITSAVE_INTAKE_ROOT:HITSAVE_GDRIVE_INTAKE_ROOT HITSAVE_INTAKE_CONFIG_DIR:HITSAVE_GDRIVE_INTAKE_CONFIG_DIR \
    HITSAVE_INTAKE_SECRETS_DIR:HITSAVE_GDRIVE_INTAKE_SECRETS_DIR \
    HITSAVE_INTAKE_BUILD_CONTEXT:HITSAVE_GDRIVE_INTAKE_BUILD_CONTEXT INTAKE_IMAGE:GDRIVE_INTAKE_IMAGE \
    HITSAVE_WINDMILL_INTAKE_STAGING_HOST:HITSAVE_WINDMILL_GDRIVE_INTAKE_STAGING_HOST; do
    _new="${_pair%%:*}" _old="${_pair#*:}"
    if [[ -z "${!_new:-}" && -n "${!_old:-}" ]]; then export "$_new=${!_old}"; fi
  done
  unset _pair _new _old
}
remote_intake_compat_env

remote_intake_archiver_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

remote_intake_bundle_root() {
  if [[ -n "${HITSAVE_BUNDLE_ROOT:-}" ]]; then
    echo "$HITSAVE_BUNDLE_ROOT"
    return
  fi
  local archiver
  archiver="$(remote_intake_archiver_root)"
  if [[ -d "$archiver/../hitsave-intake" ]]; then
    cd "$archiver/.." && pwd
    return
  fi
  echo "$archiver"
}

remote_intake_gdrive_root() {
  if [[ -n "${HITSAVE_INTAKE_ROOT:-}" ]]; then
    echo "$HITSAVE_INTAKE_ROOT"
    return
  fi
  local bundle_gdrive
  bundle_gdrive="$(remote_intake_bundle_root)/hitsave-intake"
  if [[ -d "$bundle_gdrive" ]]; then
    echo "$bundle_gdrive"
    return
  fi
  echo "$bundle_gdrive"
}

# Intake config lives in the private config repo (intake/, intake/secrets/). The legacy
# hitsave-intake/config and hitsave-intake/secrets are used only when that folder is missing.
# Host paths: the Windmill worker mounts <private>/intake at the same path, so -d works there.
remote_intake_private_intake_dir() {
  echo "$(remote_intake_private_config_host)/intake"
}

remote_intake_gdrive_config_dir() {
  if [[ -n "${HITSAVE_INTAKE_CONFIG_DIR:-}" ]]; then
    echo "$HITSAVE_INTAKE_CONFIG_DIR"
    return
  fi
  if [[ -d "$(remote_intake_private_intake_dir)" ]]; then
    remote_intake_private_intake_dir
    return
  fi
  echo "$(remote_intake_gdrive_root)/config"
}

remote_intake_gdrive_secrets_dir() {
  if [[ -n "${HITSAVE_INTAKE_SECRETS_DIR:-}" ]]; then
    echo "$HITSAVE_INTAKE_SECRETS_DIR"
    return
  fi
  if [[ -d "$(remote_intake_private_intake_dir)/secrets" ]]; then
    echo "$(remote_intake_private_intake_dir)/secrets"
    return
  fi
  echo "$(remote_intake_gdrive_root)/secrets"
}

remote_intake_gdrive_build_context() {
  if [[ -n "${HITSAVE_INTAKE_BUILD_CONTEXT:-}" ]]; then
    echo "$HITSAVE_INTAKE_BUILD_CONTEXT"
    return
  fi
  remote_intake_gdrive_docker_host_root
}

# Host paths for docker compose bind mounts (Windmill worker: set HITSAVE_INTAKE_HOST).
remote_intake_gdrive_docker_host_root() {
  if [[ -n "${HITSAVE_INTAKE_HOST:-}" ]]; then
    echo "$HITSAVE_INTAKE_HOST"
    return
  fi
  echo "$(remote_intake_gdrive_root)"
}

remote_intake_gdrive_docker_config_dir() {
  if [[ -n "${HITSAVE_INTAKE_CONFIG_DIR:-}" ]]; then
    echo "$HITSAVE_INTAKE_CONFIG_DIR"
    return
  fi
  if [[ -d "$(remote_intake_private_intake_dir)" ]]; then
    remote_intake_private_intake_dir
    return
  fi
  echo "$(remote_intake_gdrive_docker_host_root)/config"
}

remote_intake_gdrive_docker_secrets_dir() {
  if [[ -n "${HITSAVE_INTAKE_SECRETS_DIR:-}" ]]; then
    echo "$HITSAVE_INTAKE_SECRETS_DIR"
    return
  fi
  if [[ -d "$(remote_intake_private_intake_dir)/secrets" ]]; then
    echo "$(remote_intake_private_intake_dir)/secrets"
    return
  fi
  echo "$(remote_intake_gdrive_docker_host_root)/secrets"
}

# Run intake via archiver compose (Drive / Wasabi API poll — not a filesystem scan).
remote_intake_compose_intake() {
  local archiver check_root cfg sec ctx
  archiver="$(remote_intake_archiver_root)"
  check_root="$(remote_intake_gdrive_root)"
  cfg="$(remote_intake_gdrive_docker_config_dir)"
  sec="$(remote_intake_gdrive_docker_secrets_dir)"
  ctx="$(remote_intake_gdrive_build_context)"
  if [[ ! -d "$cfg" ]] && [[ ! -d "$check_root/config" ]]; then
    echo "intake config missing: $cfg (expected <private config>/intake)" >&2
    return 1
  fi
  if [[ ! -d "$sec" ]] && [[ ! -d "$check_root/secrets" ]]; then
    echo "intake secrets missing: $sec (expected <private config>/intake/secrets)" >&2
    return 1
  fi
  if [[ ! -d "$ctx" ]] && ! docker image inspect "${INTAKE_IMAGE:-hitsave/intake:local}" >/dev/null 2>&1; then
    echo "intake build context missing: $ctx (set HITSAVE_INTAKE_BUILD_CONTEXT or build hitsave/intake:local)" >&2
    return 1
  fi
  export HITSAVE_INTAKE_CONFIG_DIR="$cfg"
  export HITSAVE_INTAKE_SECRETS_DIR="$sec"
  export HITSAVE_INTAKE_BUILD_CONTEXT="$ctx"
  local priv
  priv="$(remote_intake_private_config_host)"
  HITSAVE_PRIVATE_CONFIG="$priv" remote_intake_archiver_compose \
    -f "$(remote_intake_archiver_compose_dir)/docker-compose.intake.yml" "$@"
}

# Host checkout for `docker compose` bind mounts (Windmill worker: not /archiver on the daemon host).
remote_intake_archiver_compose_dir() {
  if [[ -n "${ARCHIVER_HOST_PATH:-}" ]]; then
    echo "$ARCHIVER_HOST_PATH"
    return
  fi
  remote_intake_archiver_root
}

# Host checkout for docker compose volume sources (never /config/private inside windmill_worker).
remote_intake_private_config_host() {
  if [[ -n "${HITSAVE_PRIVATE_CONFIG_HOST:-}" ]]; then
    echo "$HITSAVE_PRIVATE_CONFIG_HOST"
    return
  fi
  local priv="${HITSAVE_PRIVATE_CONFIG:-}"
  if [[ -n "$priv" ]] && [[ "$priv" != /config/private ]]; then
    echo "$priv"
    return
  fi
  echo "$(remote_intake_archiver_root)/../hitsave-archiver-config"
}

# Mirror intake's failed/ folder into the ledger (table intake_rejection) so the ledger app can list
# rejected packages wherever it runs. Never fails the sweep.
remote_intake_sync_rejections() {
  local dir
  dir="$(remote_intake_archiver_compose_dir)"
  # Mount intake's root, not failed/ itself: Docker would create a missing failed/ as an empty folder,
  # which would empty the table. The script skips when failed/ is absent. (Don't test the host path
  # here: under Windmill this runs in the worker container, which doesn't mount HOST_INTAKE.)
  remote_intake_archiver_compose run --rm -T --no-deps \
    -v "$dir/scripts:/app/scripts:ro" \
    -v "${HOST_INTAKE:?}:/data/intake:ro" \
    --entrypoint python ingest-worker /app/scripts/remote-intake/sync_intake_rejections.py \
    --failed-dir /data/intake/failed \
    || echo "WARN: could not sync intake rejections to the ledger"
}

remote_intake_archiver_compose() {
  local dir compose_file priv
  dir="$(remote_intake_archiver_compose_dir)"
  compose_file="$dir/docker-compose.yml"
  priv="$(remote_intake_private_config_host)"
  local -a files=(-f "$compose_file")
  # Remote ledger (no local Postgres on this host): see docker-compose.remote-ledger.yml.
  if [[ -n "${HITSAVE_LEDGER_REMOTE_HOST:-}" ]]; then
    files+=(-f "$dir/docker-compose.remote-ledger.yml")
  fi
  HITSAVE_PRIVATE_CONFIG="$priv" docker compose --project-directory "$dir" "${files[@]}" "$@"
}

# The compose FILE is read by this process (inside the Windmill worker that is /intake, the
# visible mount); --project-directory is the HOST path so relative bind sources resolve on the
# daemon side. Without -f, Compose looked for a file at the host path, which the worker can't see.
remote_intake_gdrive_compose() {
  docker compose -f "$(remote_intake_gdrive_root)/docker-compose.yml" \
    --project-directory "$(remote_intake_gdrive_docker_host_root)" "$@"
}

remote_intake_load_host_env() {
  local archiver config_default
  archiver="$(remote_intake_archiver_root)"
  for _wp in \
    /run/hitsave/worker-paths.env \
    "$archiver/windmill_ops/config/worker-paths.env" \
    "$archiver/windmill_ops/docker/worker-paths.env"; do
    if [[ -f "$_wp" ]]; then
      # shellcheck disable=SC1090
      source "$_wp"
      break
    fi
  done
  unset _wp
  if [[ -z "${HITSAVE_PRIVATE_CONFIG:-}" ]] && [[ -f /run/hitsave/host.env ]]; then
    # shellcheck disable=SC1090
    source /run/hitsave/host.env
  fi
  config_default="$(cd "$archiver/.." && pwd)/hitsave-archiver-config"
  if [[ ! -d "$config_default" ]]; then
    config_default="${HITSAVE_PRIVATE_CONFIG:-}"
  fi
  if [[ -n "${HITSAVE_PRIVATE_CONFIG_HOST:-}" ]]; then
    export HITSAVE_PRIVATE_CONFIG="$HITSAVE_PRIVATE_CONFIG_HOST"
  else
    export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$config_default}"
  fi
  if [[ "$HITSAVE_PRIVATE_CONFIG" == /config/private ]] && [[ -n "${HITSAVE_PRIVATE_CONFIG_HOST:-}" ]]; then
    export HITSAVE_PRIVATE_CONFIG="$HITSAVE_PRIVATE_CONFIG_HOST"
  fi
  if [[ -f "$HITSAVE_PRIVATE_CONFIG/host.env" ]]; then
    # shellcheck disable=SC1090
    source "$HITSAVE_PRIVATE_CONFIG/host.env"
  fi
  local gdrive
  gdrive="$(remote_intake_gdrive_root)"
  if [[ -f "$HITSAVE_PRIVATE_CONFIG/intake/host.env" ]]; then
    # shellcheck disable=SC1090
    source "$HITSAVE_PRIVATE_CONFIG/intake/host.env"
  elif [[ -f "$gdrive/config/host.env" ]]; then
    # shellcheck disable=SC1090
    source "$gdrive/config/host.env"
  fi
  remote_intake_compat_env
}

# One shared lock for every remote-intake run that stages into ready/ or runs ingest-worker.
# Overlapping runs rmtree each other's staging/<key> and ready/<key> (process_package.py).
# Same host file whether run from the host or the Windmill worker (/run/hitsave/generated is
# a bind mount of <archiver>/.generated/windmill), so flock excludes across both.
remote_intake_lock_path() {
  if [[ -n "${HITSAVE_REMOTE_INTAKE_LOCK:-}" ]]; then
    echo "$HITSAVE_REMOTE_INTAKE_LOCK"
    return
  fi
  if [[ -d /run/hitsave/generated ]]; then
    echo "/run/hitsave/generated/remote-intake.lock"
    return
  fi
  local dir
  dir="$(remote_intake_archiver_root)/.generated/windmill"
  mkdir -p "$dir"
  echo "$dir/remote-intake.lock"
}

# Take the lock (non-blocking) or exit 0 with a SKIP message if another run holds it.
# Reentrant: nested calls (sweep → per-folder, full-batch → per-folder) are no-ops.
remote_intake_acquire_lock() {
  if [[ "${HITSAVE_REMOTE_INTAKE_LOCK_HELD:-}" == "1" ]]; then
    return 0
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "remote intake: flock not found; refusing to run without the intake lock" >&2
    exit 1
  fi
  local lock
  lock="$(remote_intake_lock_path)"
  # World-readable so host (uid 1000) and Windmill worker (root) can both open it; flock only
  # needs a read fd.
  [[ -e "$lock" ]] || (umask 000 && : >"$lock")
  exec 9<"$lock"
  if ! flock -n 9; then
    echo "SKIP: another remote intake run holds $lock ($(date -Is)); exiting without changes"
    exit 0
  fi
  export HITSAVE_REMOTE_INTAKE_LOCK_HELD=1
}

# game_ingest.intake_source for a slug (drive:<id> / wasabi:<prefix>); prints nothing when there is
# no such row, the row predates intake_source, or the ledger can't be reached (the guard then falls
# back to ready/<slug>/hitsave-submission.yaml).
remote_intake_ledger_source_for_slug() {
  local slug_sql="${1//\'/\'\'}"
  remote_intake_archiver_compose run --rm -T psql -U hitsave -d hitsave_ledger -tAc \
    "SELECT intake_source
     FROM game_ingest
     WHERE ingest_kind = 'dump'
       AND intake_source IS NOT NULL
       AND (source_path LIKE '%/${slug_sql}' OR source_path LIKE '%/${slug_sql}/%')
     ORDER BY (status = 'complete') DESC, game_key
     LIMIT 1" </dev/null 2>/dev/null \
    | head -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' || true
  # Trim only the ends: Wasabi sources contain spaces ("wasabi:Weekly Famitsu/E3_2002").
}

# Slug-collision guard. ready/<slug> names drop the source root, so two different packages
# can share a slug. Before treating ready/<slug> (or a ledger row matched by slug) as THIS
# package, confirm it came from this source: the ledger's intake_source when it has one,
# otherwise the handoff manifest in ready/<slug>/.
#   remote_intake_ready_source_guard <ready_root> <slug> --drive-folder-id ID | --wasabi-prefix P
# Returns 0 to proceed, 1 to skip this package (collision or check error; logged).
remote_intake_ready_source_guard() {
  local ready_root="$1" slug="$2"
  shift 2
  local out rc ledger_ref
  ledger_ref="$(remote_intake_ledger_source_for_slug "$slug")"
  out="$(python3 "$(remote_intake_scripts_dir)/ready_source.py" check \
    --slug "$slug" --ready-root "$ready_root" ${ledger_ref:+--ledger-source "$ledger_ref"} "$@" 2>&1)" \
    && rc=0 || rc=$?
  case "$rc" in
    0) return 0 ;;
    4)
      echo "WARN: ready/$slug records no source; cannot rule out a slug collision ($*)"
      return 0
      ;;
    3)
      local recorded="${out#mismatch }"
      echo "COLLISION: ready/$slug belongs to $recorded, not this package ($*)."
      echo "  Not staging, ingesting, registering or archiving it. If it is the same disc from"
      echo "  another lane, remove the duplicate; otherwise rename the source folder."
      local gen="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-$(remote_intake_archiver_root)/.generated}"
      printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "$slug" "$*" "$recorded" \
        >>"$gen/remote-intake-collisions.tsv" 2>/dev/null || true
      return 1
      ;;
    *)
      echo "ERROR: ready_source check failed for $slug (rc=$rc): $out"
      return 1
      ;;
  esac
}

remote_intake_scripts_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

# DVD-Video menu still for a staged package (dvd-menu-preview service, before ingest). The
# container decides (System: DVD-Video in !submissionInfo.txt, one .iso) and writes
# <HOST_OUTPUT>/dvd-menu/<slug>/; build-bagit-package.py packs the JPEG as the menu preview. Never
# fails the sweep: a missing menu only means no menu slide.
remote_intake_dvd_menu_preview() {
  local slug="$1"
  if ! remote_intake_archiver_compose run --rm -T dvd-menu-preview \
    --package "/data/intake/ready/$slug" --out "/output/$slug"; then
    echo "WARN dvd-menu-preview failed for $slug; ingest continues without a menu preview" >&2
    return 0
  fi
  # Vision-model pass over the exported candidates (config/dvd-menu-vlm.yaml, LiteLLM): swaps in the
  # disc's main menu when the model is confident. A no-op for non-DVD packages (no candidates.json),
  # without a key, or when the model is unreachable; the heuristic pick stays.
  remote_intake_archiver_compose run --rm -T --no-deps --entrypoint python ingest-worker \
    /app/scripts/rank_dvd_menu_vlm.py --dir "/output/dvd-menu/$slug" \
    || echo "WARN dvd menu VLM ranking failed for $slug; keeping the heuristic menu pick" >&2
}

# Data-disc dumps (PC/Mac press kits etc.): extract the disc's files into a contents bag next to
# the dump (hitsave-dumps, disc-contents/<dump path>) and attach it to the Omeka item. Labelled
# press kits are public; promotional discs wait for review in the ledger; retail software, console
# and video discs get nothing (scripts/disc_contents_policy.py). Runs after the Omeka registration;
# never fails the sweep. Rerun by hand: build_disc_contents_bag.py {classify|build|attach} --slug S.
remote_intake_disc_contents() {
  local slug="$1" rc dir priv
  dir="$(remote_intake_archiver_compose_dir)"
  priv="$(remote_intake_private_config_host)"
  set +e
  remote_intake_archiver_compose run --rm -T --no-deps --entrypoint python ingest-worker \
    /app/scripts/build_disc_contents_bag.py classify --slug "$slug"
  rc=$?
  set -e
  if [[ "$rc" -eq 3 ]]; then
    return 0  # not a data disc, or nothing to show
  elif [[ "$rc" -ne 0 ]]; then
    echo "WARN disc contents: classify failed for $slug" >&2
    return 0
  fi
  echo "--- disc contents $slug ---"
  if ! remote_intake_archiver_compose run --rm -T disc-contents \
    --package "/data/intake/ready/$slug" --out "/output/$slug"; then
    echo "WARN disc contents: extraction failed for $slug" >&2
    return 0
  fi
  if ! remote_intake_archiver_compose run --rm -T --entrypoint python ingest-worker \
    /app/scripts/build_disc_contents_bag.py build --slug "$slug"; then
    echo "WARN disc contents: bag failed for $slug (rerun: build_disc_contents_bag.py build --slug $slug)" >&2
    return 0
  fi
  remote_intake_archiver_compose run --rm -T --entrypoint python \
    -v "$priv/omeka-catalog.yaml:/config/omeka-catalog.yaml:ro" omeka-catalog \
    /app/scripts/build_disc_contents_bag.py attach --slug "$slug" \
    || echo "WARN disc contents: Omeka attach failed for $slug (rerun: build_disc_contents_bag.py attach --slug $slug)" >&2
}

# Virus-scan a staged package before anything opens its files, then build the DVD menu preview.
# The scan is `ingest-folder.py --scan-only`: the same gates as the ingest (re-ingest and duplicate
# guards, content policy, ClamAV, ledger quarantine on a hit). Its report is saved, and the ingest
# reuses it while the files are unchanged, so packages aren't scanned twice. Returns non-zero when
# the scan didn't pass; the caller then skips the menu preview and the ingest.
remote_intake_scan_then_menu() {
  local slug="$1" lane="$2"
  echo "--- virus scan $slug ---"
  if ! remote_intake_ingest_or_reject "$slug" "$lane" --scan-only; then
    echo "--- scan didn't pass for $slug: no menu preview, no ingest ---"
    return 1
  fi
  remote_intake_dvd_menu_preview "$slug"
}

# Ingest one staged package (extra arguments go to ingest-folder.py, e.g. --scan-only). On a
# duplicate dump (ingest-folder.py exit 4: its primary image is already preserved in another
# package) the package is moved to intake failed/<slug>/ with error.txt + .intake-lane, so
# remote_intake_sync_rejections reports it and sweeps stop retrying. Returns the ingest exit code.
remote_intake_ingest_or_reject() {
  local slug="$1" lane="$2" log rc dup
  shift 2
  log="$(mktemp)"
  set +e
  remote_intake_archiver_compose run --rm -T ingest-worker "/data/intake/ready/$slug/game.ingest.yaml" "$@" 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -eq 4 ]]; then
    dup="$(grep -o 'DUPLICATE_OF=[^[:space:]]*' "$log" | tail -1 | cut -d= -f2 || true)"
    local reason
    reason="Duplicate dump: its disc/cartridge image is already preserved as ${dup:-another package} (identical SHA-256). Not ingested again; see the ingest log."
    if remote_intake_gdrive_compose run --rm -T --user 1000:1000 --entrypoint sh intake -c '
        set -e; s="$1"; lane="$2"; reason="$3"
        [ -d "/data/intake/ready/$s" ] || exit 0
        mkdir -p /data/intake/failed
        rm -rf "/data/intake/failed/$s"
        mv "/data/intake/ready/$s" "/data/intake/failed/$s"
        printf "%s\n" "$reason" > "/data/intake/failed/$s/error.txt"
        printf "%s\n" "$lane" > "/data/intake/failed/$s/.intake-lane"' sh "$slug" "$lane" "$reason"; then
      echo "--- duplicate of ${dup:-?}: moved $slug to failed/ ---"
    else
      echo "WARN: duplicate $slug could not be moved to failed/ (stays in ready/)" >&2
    fi
  fi
  rm -f "$log"
  return "$rc"
}
