#!/usr/bin/env bash
# Manual curation quarantine: ledger status + hide Omeka item/DIP from public site.
# shellcheck shell=bash
# Windmill bash passes schema args positionally, in declaration order (see schema in script.yaml).
omeka_ref="$1"
block_reason="${2:-curation_review}"
note="${3:-}"
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

: "${omeka_ref:?omeka_ref required (Omeka item id or /item/<id> URL)}"
: "${note:?note required (human-readable curation reason)}"

reason="${block_reason:-curation_review}"

archiver_compose run --rm --entrypoint python omeka-catalog \
  /app/scripts/curation-quarantine-omeka.py \
  --omeka-ref "$omeka_ref" \
  --reason "$reason" \
  "$note"
