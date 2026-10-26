#!/usr/bin/env bash
# Long-running batch ingest + Omeka register (operator only; concurrency limit 1 in Windmill).
# shellcheck disable=SC1091
source /archiver/windmill_ops/lib/hitsave-env.sh

batch_yaml="${batch_yaml:-config/preservation/batch.yml}"
exec "$ARCHIVER_ROOT/scripts/run-batch-resume-omeka.sh" "$batch_yaml"
