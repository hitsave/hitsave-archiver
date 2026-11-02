# Dev variant: pip, apk (ffmpeg, docker CLI, libpq), and Siegfried for preservation ingest.
FROM cgr.dev/chainguard/python:latest-dev

USER root

ARG SIEGFRIED_VERSION=1.11.9
# Release assets use 1-11-9, not 1.11.9, in the zip filename.
ARG SIEGFRIED_RELEASE=1-11-9
RUN apk add --no-cache \
      ca-certificates \
      curl \
      docker-cli \
      ffmpeg-7 \
      libpq-16 \
      7zip \
    && curl -fsSL -o /tmp/siegfried.zip \
      "https://github.com/richardlehane/siegfried/releases/download/v${SIEGFRIED_VERSION}/siegfried_${SIEGFRIED_RELEASE}_linux64.zip" \
    && 7zz x -y "/tmp/siegfried.zip" "sf" -o/usr/local/bin \
    && chmod +x /usr/local/bin/sf \
    && rm -f /tmp/siegfried.zip \
    && sf -update

WORKDIR /app

COPY ingest-worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scripts/batch-expand-preservation.py \
    scripts/batch_manifest.py \
    scripts/build-bagit-package.py \
    scripts/dip_package_previews.py \
    scripts/apply_dvd_menu_bag_previews.py \
    scripts/reset-batch-manifest-ledger.py \
    scripts/upload-bag-wasabi.py \
    scripts/upload_bag_wasabi.py \
    scripts/s3_etag.py \
    scripts/validate_dump_wasabi_packages.py \
    scripts/audit_dump_title_slugs.py \
    scripts/rename_dump_package_slug.py \
    scripts/sync_dump_bag_metadata_from_ledger.py \
    scripts/wasabi_rename.py \
    scripts/relocate_dump_bags_by_sort_letter.py \
    scripts/export-preservation-catalog.py \
    scripts/config_require.py \
    scripts/yaml_config.py \
    scripts/omeka_catalog_common.py \
    scripts/host_paths.py \
    scripts/read-host-path-config.py \
    scripts/submission_sources.py \
    scripts/preservation_common.py \
    scripts/ingest_kind.py \
    scripts/dump_redump.py \
    scripts/dump_nointro.py \
    scripts/dump_switch.py \
    scripts/dump_classify.py \
    scripts/dump_catalog_omeka.py \
    scripts/apply_dump_catalog_omeka.py \
    scripts/batch_ledger.py \
    scripts/ledger_health_report.py \
    scripts/package_identity.py \
    scripts/package_ledger.py \
    scripts/format_identification.py \
    scripts/source_content_policy.py \
    scripts/normalize_display_title.py \
    scripts/ingest-folder.py \
    scripts/premis_virus_check.py \
    scripts/clamav_full_scan.py \
    scripts/archive_scan.py \
    scripts/udf_extents.py \
    scripts/fixity-check.py \
    scripts/omeka_dip_media.py \
    scripts/omeka_external_dip.py \
    scripts/dip_mount_preflight.py \
    scripts/omeka_staff_metadata.py \
    scripts/register-dip-omeka-api.py \
    scripts/press_material_description.py \
    scripts/moby_omeka_fields.py \
    scripts/apply-moby-metadata-omeka.py \
    scripts/strip-moby-catalog-omeka.py \
    scripts/strip-moby-ddg-mismatches-omeka.py \
    scripts/curation-quarantine-omeka.py \
    scripts/set-omeka-visibility.py \
    scripts/apply-press-material-description-omeka.py \
    scripts/patch-press-catalog-omeka-and-bag.py \
    scripts/correct-catalog-item.py \
    scripts/moby_resolve.py \
    scripts/moby_page_scrape.py \
    scripts/moby_web_search.py \
    scripts/apply-moby-ad-blurbs-from-web.py \
    scripts/moby_enrich_game.py \
    scripts/omeka_backfill_helpers.py \
    scripts/backfill-ledger-omeka-ids.py \
    scripts/dedupe-omeka-test-items.py \
    scripts/reset-batch-ledger.py \
    scripts/submission_common.py \
    scripts/ingest-submission.py \
    scripts/redump_info_lookup.py \
    scripts/apply-external-dip-omeka.py \
    scripts/attach_dump_external_dip_omeka.py \
    /app/scripts/

ENTRYPOINT ["python", "/app/scripts/ingest-folder.py"]
