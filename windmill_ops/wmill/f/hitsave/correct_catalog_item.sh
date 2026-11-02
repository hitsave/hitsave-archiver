#!/usr/bin/env bash
# Patch Omeka catalog fields for one item (optional title, Moby URL, description, developer, publisher).
# shellcheck shell=bash
# arguments of the form X="$I" are parsed as parameters X of type string
omeka_ref="$1"
title="${2:-}"
moby_url="${3:-}"
clear_moby_url="${4:-false}"
description="${5:-}"
developer="${6:-}"
clear_developer="${7:-false}"
publisher="${8:-}"
clear_publisher="${9:-false}"
reset_ledger_moby="${10:-true}"
former_title="${11:-}"
correction_summary="${12:-}"
execute="${13:-true}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

: "${omeka_ref:?omeka_ref required (Omeka item id or /item/<id> URL)}"

args=(--omeka-ref "$omeka_ref")
[[ -n "${title:-}" ]] && args+=(--title "$title")
[[ -n "${moby_url:-}" ]] && args+=(--moby-url "$moby_url")
if [[ "${clear_moby_url:-false}" == "true" ]]; then
  args+=(--clear-moby-url)
fi
# Omit --description when empty so --clear-moby-url can restore press-kit text (status-web same rule).
if [[ -n "${description:-}" ]]; then
  args+=(--description "$description")
fi
[[ -n "${developer:-}" ]] && args+=(--developer "$developer")
if [[ "${clear_developer:-false}" == "true" ]]; then
  args+=(--clear-developer)
fi
[[ -n "${publisher:-}" ]] && args+=(--publisher "$publisher")
if [[ "${clear_publisher:-false}" == "true" ]]; then
  args+=(--clear-publisher)
fi
if [[ "${reset_ledger_moby:-true}" == "false" ]]; then
  args+=(--no-reset-ledger-moby)
fi
[[ -n "${former_title:-}" ]] && args+=(--former-title "$former_title")
[[ -n "${correction_summary:-}" ]] && args+=(--correction-summary "$correction_summary")
if [[ "${execute:-true}" != "false" ]]; then
  args+=(--execute)
fi

archiver_compose run --rm --entrypoint python omeka-catalog \
  /app/scripts/correct-catalog-item.py "${args[@]}"
