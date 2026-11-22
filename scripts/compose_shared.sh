# shellcheck shell=bash
# One-off containers of the archiver stack never recreate its shared services (postgres, clamav).
#
# A `docker compose run` without --no-deps recreates a dependency whenever that service's config
# hash differs from the running container's, and Compose versions compute the hash differently.
# From 2026-11-21 the host had Compose 5.5 and the Windmill worker 2.26: each side recreated clamav
# whenever it ran, which killed the other side's virus scan mid-file.
#
# So `run` first starts the shared services if they aren't running (`up --no-recreate`, which never
# replaces a running container) and waits until they're healthy, then runs with --no-deps.

HITSAVE_SHARED_SERVICES=(postgres clamav)

# hitsave_compose <docker compose command and global options...> -- <compose arguments...>
hitsave_compose() {
  local -a base=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    base+=("$1")
    shift
  done
  [[ $# -gt 0 ]] && shift
  if [[ "${1:-}" == "run" ]]; then
    shift
    local out
    if ! out="$("${base[@]}" up -d --no-recreate --wait "${HITSAVE_SHARED_SERVICES[@]}" 2>&1)"; then
      printf '%s\n' "$out" >&2
      echo "couldn't start the shared services (${HITSAVE_SHARED_SERVICES[*]})" >&2
      return 1
    fi
    set -- run --no-deps "$@"
  fi
  "${base[@]}" "$@"
}

# compose_run <run arguments...>: `docker compose run` for host scripts (default compose files).
compose_run() {
  hitsave_compose docker compose -- run "$@"
}
