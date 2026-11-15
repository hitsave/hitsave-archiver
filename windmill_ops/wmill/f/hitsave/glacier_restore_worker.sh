#!/usr/bin/env bash
# Move each open restore request from the ledger one step forward: check Wasabi, request restores
# from the Glacier copy, check restored files, and write approved files back to Wasabi
# (scripts/glacier-restore-worker.py, docs/restore-from-glacier.md).
# shellcheck shell=bash
# shellcheck disable=SC1091
source /run/hitsave/hitsave-bootstrap.sh

# Scripts mounted from the checkout so the job does not depend on the ingest-worker image contents.
archiver_compose run --rm -T --no-deps \
  -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" \
  --entrypoint python ingest-worker /app/scripts/glacier-restore-worker.py
