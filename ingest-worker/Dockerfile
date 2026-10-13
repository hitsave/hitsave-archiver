FROM python:3.12-slim-bookworm

ARG SIEGFRIED_VERSION=1.11.9
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 ffmpeg docker.io unzip ca-certificates \
    && curl -fsSL -o /tmp/siegfried.deb \
       "https://github.com/richardlehane/siegfried/releases/download/v${SIEGFRIED_VERSION}/siegfried_${SIEGFRIED_VERSION}-1_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/siegfried.deb \
    && rm -f /tmp/siegfried.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ingest-worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scripts/batch-expand-preservation.py \
    scripts/build-bagit-package.py \
    scripts/dip_package_previews.py \
    scripts/reset-batch-manifest-ledger.py \
    scripts/upload-press-bag-wasabi.py \
    scripts/purge-wasabi-prefix.py \
    scripts/export-preservation-catalog.py \
    scripts/config_require.py \
    scripts/host_paths.py \
    scripts/read-host-path-config.py \
    scripts/submission_sources.py \
    scripts/preservation_common.py \
    scripts/ingest_kind.py \
    scripts/dump_redump.py \
    scripts/dump_classify.py \
    scripts/dump_catalog_omeka.py \
    scripts/upload-dump-wasabi.py \
    scripts/apply_dump_catalog_omeka.py \
    scripts/batch_ledger.py \
    scripts/package_identity.py \
    scripts/package_ledger.py \
    scripts/format_identification.py \
    scripts/source_content_policy.py \
    scripts/normalize_display_title.py \
    scripts/ingest-folder.py \
    scripts/premis_virus_check.py \
    scripts/fixity-check.py \
    scripts/omeka_dip_media.py \
    scripts/omeka_external_dip.py \
    scripts/dip_mount_preflight.py \
    scripts/omeka_staff_metadata.py \
    scripts/register-dip-omeka-api.py \
    scripts/press_material_description.py \
    scripts/moby_omeka_fields.py \
    scripts/apply-moby-metadata-omeka.py \
    scripts/apply-press-material-description-omeka.py \
    scripts/moby_resolve.py \
    scripts/moby_page_scrape.py \
    scripts/moby_web_search.py \
    scripts/apply-moby-ad-blurbs-from-web.py \
    scripts/moby_enrich_game.py \
    scripts/backfill-ledger-omeka-ids.py \
    scripts/dedupe-omeka-test-items.py \
    scripts/reset-batch-ledger.py \
    scripts/submission_common.py \
    scripts/ingest-submission.py \
    scripts/redump_info_lookup.py \
    scripts/apply-redump-catalog-omeka.py \
    scripts/apply-external-dip-omeka.py \
    /app/scripts/

ENTRYPOINT ["python", "/app/scripts/ingest-folder.py"]
