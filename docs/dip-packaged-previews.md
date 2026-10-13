# Packaged DIP previews (metadata/hitsave)

BagIt builds from `build-bagit-package.py` embed browse collages and grid JPEG thumbnails under `metadata/hitsave/` on Wasabi (unpacked bag), plus `preview-manifest.json`. OmekaDipViewer serves these for item browse and the Embla carousel (`fN.thumb` ARK/file URLs); lightbox and downloads use full-size `data/` members.

Config: `config/preservation/dip-previews.yaml` (keep aligned with Omeka `DipConfig` defaults).

Packages without `preview-manifest.json` fall back to on-the-fly preview generation in Omeka.

**Backfill:** re-run bag build + Wasabi upload for existing press packages to add previews.
