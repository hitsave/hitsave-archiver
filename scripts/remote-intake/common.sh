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
  remote_intake_archiver_compose -f "$(remote_intake_archiver_compose_dir)/docker-compose.gdrive-intake.yml" "$@"
}

# Host checkout for `docker compose` bind mounts (Windmill worker: not /archiver on the daemon host).
remote_intake_archiver_compose_dir() {
  if [[ -n "${ARCHIVER_HOST_PATH:-}" ]]; then
    echo "$ARCHIVER_HOST_PATH"
    return
  fi
  remote_intake_archiver_root
}

remote_intake_archiver_compose() {
  local dir compose_file
  dir="$(remote_intake_archiver_compose_dir)"
  compose_file="$dir/docker-compose.yml"
  docker compose --project-directory "$dir" -f "$compose_file" "$@"
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
  export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-$config_default}"
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

remote_intake_scripts_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}
