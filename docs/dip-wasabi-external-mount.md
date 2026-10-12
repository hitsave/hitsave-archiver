# Pilot: DIP on Wasabi + external tar mount (Omeka)

OmekaDipViewer can read a DIP `.tar` from **`o:data.external_tar_path`** when that path is readable inside the Omeka container (read-only rclone mount of `dip-access/` on Wasabi).

## Config

Copy `config/preservation/dip-wasabi.yml.example` → `config/preservation/dip-wasabi.yml` (or use the example for tests).

## Upload + patch (Abzu example)

```bash
source hitsave-archiver-config/host.env
export HOST_OUTPUT=/tank/hitsave-archiver/output
cd hitsave-archiver

docker compose build omeka-uploader
docker compose run --rm --entrypoint python omeka-uploader \
  /app/scripts/apply-external-dip-omeka.py repcopies-a-full-abzu \
  /output/dip/batch/repcopies-a-full/repcopies-a-full-abzu.tar \
  --dip-wasabi-config /config/preservation/dip-wasabi.yml.example
```

## Omeka test stack (Docker)

**Live Wasabi reads:** `hitsave-omeka-test/docker-compose.dip-wasabi-rclone.yml` (rclone sidecar + mount propagation). Write-up: `hitsave-omeka-test/docs/dip-wasabi-docker-fuse.md`.

**Local tar bind (no FUSE):** `docker-compose.dip-wasabi-pilot.yml`.

```bash
cd ../hitsave-omeka-test
export DIP_WASABI_MOUNT=/home/jonas/mnt/dip-wasabi-live
export DIP_WASABI_RCLONE_CONFIG=../hitsave-archiver/.generated/rclone-dip-wasabi.conf
mkdir -p "$DIP_WASABI_MOUNT"
docker compose -f docker-compose.yml -f docker-compose.dip-wasabi-rclone.yml up -d
```

Verify inside Omeka: `test -r /dip-wasabi/repcopies-a-full-abzu/repcopies-a-full-abzu.tar`

Set `OMEKA_MARIADB_CONTAINER=hitsave-omeka-test-mariadb-1` when running `apply-external-dip-omeka.py` so custom `media.data` fields persist (REST PATCH alone drops them).

Local Omeka copy under `volume/files/original/` may remain; the viewer prefers `external_tar_path` when set.

Branch: `feat/dip-wasabi-external-mount` (archiver, omeka-dip-viewer, hitsave-omeka-test).
