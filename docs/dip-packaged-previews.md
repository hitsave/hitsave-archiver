# Packaged DIP previews (metadata/hitsave)

BagIt builds from `build-bagit-package.py` embed browse collages and grid JPEG thumbnails in the tar under `metadata/hitsave/`, plus `preview-manifest.json`. OmekaDipViewer serves these for item browse and the Embla carousel (`fN.thumb` ARK/file URLs); lightbox and downloads use full-size `data/` members.

Config: `config/preservation/dip-previews.yaml` (keep aligned with Omeka `DipConfig` defaults).

Legacy tars without a manifest fall back to on-the-fly preview generation in Omeka.

**Backfill:** re-run bag build + Wasabi upload for existing press packages to add previews.
