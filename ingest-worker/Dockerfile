FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ingest-worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY packaging/e-ark-schemas /app/packaging/e-ark-schemas

COPY scripts/batch-expand-preservation.py \
    scripts/build-dip-e-ark.py \
    scripts/build-aip-e-ark.py \
    scripts/validate-e-ark-package.sh \
    scripts/preservation_common.py \
    scripts/normalize_display_title.py \
    scripts/ingest-game-folder.py \
    scripts/fixity-check.py \
    scripts/read-config-yaml.py \
    scripts/upload-dip-omeka-api.py \
    scripts/moby_resolve.py \
    scripts/moby_omeka_fields.py \
    scripts/moby_enrich_game.py \
    scripts/reset-batch-ledger.py \
    scripts/upload-aip-wasabi.py \
    scripts/submission_common.py \
    scripts/ingest-submission.py \
    /app/scripts/

ENTRYPOINT ["python", "/app/scripts/ingest-game-folder.py"]
