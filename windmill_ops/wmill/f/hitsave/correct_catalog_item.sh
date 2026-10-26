#!/usr/bin/env bash
# Patch Omeka catalog fields for one item (optional title, Moby URL, description, developer, publisher).
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

: "${omeka_ref:?omeka_ref required (Omeka item id or /item/<id> URL)}"

args=(--omeka-ref "$omeka_ref")
[[ -n "${title:-}" ]] && args+=(--title "$title")
[[ -n "${moby_url:-}" ]] && args+=(--moby-url "$moby_url")
if [[ -n "${description+set}" ]]; then
  args+=(--description "${description:-}")
fi
[[ -n "${developer:-}" ]] && args+=(--developer "$developer")
[[ -n "${publisher:-}" ]] && args+=(--publisher "$publisher")
[[ -n "${former_title:-}" ]] && args+=(--former-title "$former_title")
[[ -n "${correction_summary:-}" ]] && args+=(--correction-summary "$correction_summary")
if [[ "${execute:-true}" != "false" ]]; then
  args+=(--execute)
fi

archiver_compose run --rm --entrypoint python omeka-catalog \
  /app/scripts/correct-catalog-item.py "${args[@]}"
