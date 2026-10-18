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
  echo "$(remote_intake_bundle_root)/hitsave-gdrive-intake"
}

remote_intake_load_host_env() {
  local archiver config_default
  archiver="$(remote_intake_archiver_root)"
  config_default="$(cd "$archiver/.." && pwd)/hitsave-archiver-config"
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
