# BagIt fixture tars

Real press-material bags built with `scripts/build-bagit-package.py` (not synthetic E2E stubs).

| File | Source SIP | Notes |
|------|------------|--------|
| `repcopies-s-25-scrubs.tar` | `/tank2/press-material/Representation_Copies/S/Scrubs` | 2 image files; use with OmekaDipViewer `test/parse_fixture.php` |

Rebuild (requires `HOST_PRESS_MATERIAL=/tank2/press-material`):

```bash
docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/build-bagit-package.py \
  /config/preservation/generated/repcopies-s-25/repcopies-s-25-scrubs.yaml
docker compose run --rm --entrypoint cat ingest-worker \
  /output/dip/batch/repcopies-s-25/repcopies-s-25-scrubs.tar \
  > fixtures/bagit/repcopies-s-25-scrubs.tar
```
