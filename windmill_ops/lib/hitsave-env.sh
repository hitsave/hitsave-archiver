# shellcheck shell=bash
# Shared by Windmill bash scripts (source at top of each script).
set -euo pipefail

ARCHIVER_ROOT="${ARCHIVER_ROOT:-/archiver}"
HOST_ENV_FILE="${HOST_ENV_FILE:-/run/hitsave/host.env}"
for _paths_file in \
  "${WORKER_PATHS_FILE:-}" \
  /archiver/windmill_ops/config/worker-paths.env \
  /run/hitsave/worker-paths.env; do
  if [[ -n "$_paths_file" ]] && [[ -f "$_paths_file" ]]; then
    # shellcheck disable=SC1090
    source "$_paths_file"
    break
  fi
done
unset _paths_file

if [[ -f "$HOST_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$HOST_ENV_FILE"
fi

# Worker bind mount for in-job reads (ledger_health_report). Compose bind mounts need a host path.
if [[ -f /config/private/preservation/database.yaml ]]; then
  export HITSAVE_PRIVATE_CONFIG_MOUNT=/config/private
fi
if [[ -n "${HITSAVE_PRIVATE_CONFIG_HOST:-}" ]]; then
  export HITSAVE_PRIVATE_CONFIG="$HITSAVE_PRIVATE_CONFIG_HOST"
elif [[ "${HITSAVE_PRIVATE_CONFIG:-}" == /config/private ]] || [[ -z "${HITSAVE_PRIVATE_CONFIG:-}" ]]; then
  export HITSAVE_PRIVATE_CONFIG="${HITSAVE_PRIVATE_CONFIG:-../hitsave-archiver-config}"
fi
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
export HITSAVE_INTAKE_ROOT="${HITSAVE_INTAKE_ROOT:-/intake}"
if [[ -n "${HITSAVE_INTAKE_HOST:-}" ]]; then
  export HITSAVE_INTAKE_CONFIG_DIR="${HITSAVE_INTAKE_CONFIG_DIR:-${HITSAVE_INTAKE_HOST}/config}"
  export HITSAVE_INTAKE_SECRETS_DIR="${HITSAVE_INTAKE_SECRETS_DIR:-${HITSAVE_INTAKE_HOST}/secrets}"
  export HITSAVE_INTAKE_BUILD_CONTEXT="${HITSAVE_INTAKE_BUILD_CONTEXT:-${HITSAVE_INTAKE_HOST}}"
fi
# Windmill jobs use an unprivileged UID; repo .generated is often not writable. Compose mounts
# host …/.generated/windmill at /run/hitsave/generated (mode 1777 on the host).
if [[ -z "${HITSAVE_REMOTE_INTAKE_LOG_DIR:-}" ]] && [[ -d /run/hitsave/generated ]]; then
  export HITSAVE_REMOTE_INTAKE_LOG_DIR=/run/hitsave/generated
else
  export HITSAVE_REMOTE_INTAKE_LOG_DIR="${HITSAVE_REMOTE_INTAKE_LOG_DIR:-${ARCHIVER_ROOT}/.generated}"
fi

# Poll uses cloud APIs; HOST_INTAKE is staging for process — use a host path Docker can bind.
if [[ "${HITSAVE_WINDMILL_POLL_STAGING:-}" == "1" ]]; then
  export HOST_INTAKE="${HITSAVE_WINDMILL_INTAKE_STAGING_HOST:-/tmp/hitsave-windmill-intake}"
fi

cd "$ARCHIVER_ROOT"

archiver_compose() {
  if [[ -z "${ARCHIVER_HOST_PATH:-}" ]] || [[ ! -d "${ARCHIVER_HOST_PATH}/config" ]]; then
    echo "ARCHIVER_HOST_PATH must be set to the host hitsave-archiver checkout (see worker-paths.env.example)" >&2
    exit 1
  fi
  docker compose --project-directory "$ARCHIVER_HOST_PATH" "$@"
}
