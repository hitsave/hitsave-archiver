# shellcheck shell=bash
# Shared paths for community-dump remote intake (Drive / Wasabi → archiver).
# Source from operator host.env: HITSAVE_PRIVATE_CONFIG, HOST_GDRIVE_INTAKE, HOST_OUTPUT.

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
  if [[ -d "$archiver/../hitsave-gdrive-intake" ]]; then
    cd "$archiver/.." && pwd
    return
  fi
  echo "$archiver"
}

remote_intake_gdrive_root() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_ROOT:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_ROOT"
    return
  fi
  local bundle_gdrive
  bundle_gdrive="$(remote_intake_bundle_root)/hitsave-gdrive-intake"
  if [[ -d "$bundle_gdrive" ]]; then
    echo "$bundle_gdrive"
    return
  fi
  echo "$bundle_gdrive"
}

remote_intake_gdrive_config_dir() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_CONFIG_DIR:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_CONFIG_DIR"
    return
  fi
  echo "$(remote_intake_gdrive_root)/config"
}

remote_intake_gdrive_secrets_dir() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_SECRETS_DIR:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_SECRETS_DIR"
    return
  fi
  echo "$(remote_intake_gdrive_root)/secrets"
}

remote_intake_gdrive_build_context() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_BUILD_CONTEXT:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_BUILD_CONTEXT"
    return
  fi
  remote_intake_gdrive_docker_host_root
}

# Host paths for docker compose bind mounts (Windmill worker: set HITSAVE_GDRIVE_INTAKE_HOST).
remote_intake_gdrive_docker_host_root() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_HOST:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_HOST"
    return
  fi
  echo "$(remote_intake_gdrive_root)"
}

remote_intake_gdrive_docker_config_dir() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_CONFIG_DIR:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_CONFIG_DIR"
    return
  fi
  echo "$(remote_intake_gdrive_docker_host_root)/config"
}

remote_intake_gdrive_docker_secrets_dir() {
  if [[ -n "${HITSAVE_GDRIVE_INTAKE_SECRETS_DIR:-}" ]]; then
    echo "$HITSAVE_GDRIVE_INTAKE_SECRETS_DIR"
    return
  fi
  echo "$(remote_intake_gdrive_docker_host_root)/secrets"
}

# Run gdrive-intake via archiver compose (Drive / Wasabi API poll — not a filesystem scan).
remote_intake_compose_gdrive_intake() {
  local archiver check_root cfg sec ctx
  archiver="$(remote_intake_archiver_root)"
  check_root="$(remote_intake_gdrive_root)"
  cfg="$(remote_intake_gdrive_docker_config_dir)"
  sec="$(remote_intake_gdrive_docker_secrets_dir)"
  ctx="$(remote_intake_gdrive_build_context)"
  if [[ ! -d "$check_root/config" ]] && [[ ! -d "$cfg" ]]; then
    echo "gdrive-intake config missing (check $check_root or HITSAVE_GDRIVE_INTAKE_HOST)" >&2
    return 1
  fi
  if [[ ! -d "$check_root/secrets" ]] && [[ ! -d "$sec" ]]; then
    echo "gdrive-intake secrets missing (check $check_root or HITSAVE_GDRIVE_INTAKE_HOST)" >&2
    return 1
  fi
  if [[ ! -d "$ctx" ]] && ! docker image inspect "${GDRIVE_INTAKE_IMAGE:-hitsave/gdrive-intake:local}" >/dev/null 2>&1; then
    echo "gdrive-intake build context missing: $ctx (set HITSAVE_GDRIVE_INTAKE_BUILD_CONTEXT or build hitsave/gdrive-intake:local)" >&2
    return 1
  fi
  export HITSAVE_GDRIVE_INTAKE_CONFIG_DIR="$cfg"
  export HITSAVE_GDRIVE_INTAKE_SECRETS_DIR="$sec"
  export HITSAVE_GDRIVE_INTAKE_BUILD_CONTEXT="$ctx"
  local priv
  priv="$(remote_intake_private_config_host)"
  HITSAVE_PRIVATE_CONFIG="$priv" remote_intake_archiver_compose \
    -f "$(remote_intake_archiver_compose_dir)/docker-compose.gdrive-intake.yml" "$@"
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

remote_intake_archiver_compose() {
  local dir compose_file priv
  dir="$(remote_intake_archiver_compose_dir)"
  compose_file="$dir/docker-compose.yml"
  priv="$(remote_intake_private_config_host)"
  HITSAVE_PRIVATE_CONFIG="$priv" docker compose --project-directory "$dir" -f "$compose_file" "$@"
}

remote_intake_gdrive_compose() {
  docker compose --project-directory "$(remote_intake_gdrive_docker_host_root)" "$@"
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
  if [[ -f "$gdrive/config/host.env" ]]; then
    # shellcheck disable=SC1090
    source "$gdrive/config/host.env"
  fi
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

# Slug-collision guard. ready/<slug> names drop the source root, so two different packages
# can share a slug. Before treating ready/<slug> (or a ledger row matched by slug) as THIS
# package, confirm its handoff manifest came from this source.
#   remote_intake_ready_source_guard <ready_root> <slug> --drive-folder-id ID | --wasabi-prefix P
# Returns 0 to proceed, 1 to skip this package (collision or check error; logged).
remote_intake_ready_source_guard() {
  local ready_root="$1" slug="$2"
  shift 2
  local out rc
  out="$(python3 "$(remote_intake_scripts_dir)/ready_source.py" check \
    --slug "$slug" --ready-root "$ready_root" "$@" 2>&1)" && rc=0 || rc=$?
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
