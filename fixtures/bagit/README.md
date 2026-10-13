# BagIt fixtures

Press-material bags built with `scripts/build-bagit-package.py`.

| Path | Source SIP | Notes |
|------|------------|--------|
| `repcopies-s-25-scrubs/` (local) | `Representation_Copies/S/Scrubs` | Small bag; use with `omeka-dip-viewer/test/parse_fixture.php` |
| Tekken 7 demo | `Representation_Copies/T/Tekken_7` | `bagit-demo-tekken-7.yaml` — large; keep under `/output/bag/` on the host |

Rebuild Scrubs (requires press material mount and ledger identity):

```bash
docker compose run --rm ingest-worker \
  /config/preservation/generated/repcopies-s-25/repcopies-s-25-scrubs.yaml
```

Local bag directory: `$HOST_OUTPUT/bag/batch/repcopies-s-25/repcopies-s-25-scrubs/` (exact path from `resolve_game_config()`).

Test indexing:

```bash
php omeka-dip-viewer/test/parse_fixture.php /path/to/bag/dir
```
