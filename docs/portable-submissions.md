# Portable submissions (zip intake)

Archivists without access to `/tank2/press-material` can deliver **zip** packages (games, press kits, or other digital material). The stack unpacks them under a dedicated submissions volume and runs the **same BagIt ingest** as on-site press material: ClamAV → `build-bagit-package.py` → Wasabi (when enabled) → optional Moby enrich → Omeka catalog registration.

Policy and limits: [`config/preservation/submissions.yaml`](../config/preservation/submissions.yaml).

## Layout (host)

Create once on your preservation host:

```bash
sudo mkdir -p /tank/hitsave-archiver/submissions/{incoming,workspace,processed,failed}
sudo chown -R "$USER:$USER" /tank/hitsave-archiver/submissions
```

| Container path | Role |
|----------------|------|
| `/data/submissions/incoming` | Drop zone for `.zip` files |
| `/data/submissions/workspace` | Extract + scratch (per zip) |
| `/data/submissions/processed` | Successful zips moved here |
| `/data/submissions/failed` | Failed zips + `workspace/.../error.txt` |

Game YAML for the worker is written under **`/output/submissions/game-configs/`** (host: `/tank/hitsave-archiver/output/submissions/game-configs/`).

## Zip layout standard (planned)

Archivist packages should converge on: **one game per zip**, either a **single root folder** named for the game or a **zip filename** that is the game name with loose files inside. Unrecognizable layouts (e.g. `bob.zip` with only an `.exe`) should **quarantine**. Full rules: [submission-zip-layout-standard.md](./submission-zip-layout-standard.md).

## Optional manifest inside the zip

Include [`examples/hitsave-submission.yaml`](../examples/hitsave-submission.yaml) at the zip root (or inside a single top-level folder) to set catalog title and ingest kind:

```yaml
ingest_kind: press_material
omeka_item_title: "My Game — press and marketing materials"
material_type: press
submitter: archivist@example.org
```

Ledger **`game_key`** (`package_id`) is assigned at ingest — do not set it in the zip manifest.

Manifest files must be **≤ 64 KiB** (`limits.max_manifest_bytes`). Parsing uses `yaml.safe_load` only (no executable content).

If the zip contains **one top-level folder** and no loose files, that folder becomes `source_game_folder` (same as unpacking a game directory tree).

## Operator workflow

1. Archivist sends `materials.zip` (SFTP, shared drive, etc.).
2. Operator copies it to `/tank/hitsave-archiver/submissions/incoming/`.
3. Run:

```bash
cd /path/to/hitsave-archive-agent
./scripts/ingest-submission-incoming.sh materials.zip
```

Equivalent manual invoke:

```bash
docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/ingest-submission.py /data/submissions/incoming/materials.zip
```

Overrides:

```bash
docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/ingest-submission.py /data/submissions/incoming/materials.zip \
  --title "My Game — press materials"
```

Unpack-only (no ingest yet):

```bash
docker compose run --rm --entrypoint python ingest-worker \
  /app/scripts/ingest-submission.py /data/submissions/incoming/materials.zip --skip-ingest
```

4. **Omeka DIP catalog registration**: after ingest completes, use `register-dip-omeka-api.py` or `register-manifest-dips-omeka.sh` with the ledger `game_key`.

## Security model

Submissions are **untrusted input** until ingest completes. Controls are layered; none replace vetting **who** may send zips and reviewing **`failed/`** alerts.

### Trust and placement

- Only operators copy zips onto the preservation host; archivists do not get shell or press-material paths.
- Extracted data stays under **`/data/submissions/workspace`** (ClamAV mounts this read-only for scanning).
- Press-material remains read-only on the worker; submissions do not write into `/data/press-material`.

### Zip structure and zip bombs

| Control | Config key | Behavior |
|---------|------------|----------|
| Max zip size | `limits.max_zip_bytes` | Reject before open |
| Declared uncompressed total | `limits.max_uncompressed_bytes` | Sum of central-directory `file_size` |
| **Actual bytes written** | `limits.max_extracted_bytes` | Streamed extract; catches lying metadata |
| Max files in archive | `limits.max_archive_members` | Member count cap |
| Max path length | `limits.max_path_length` | Path normalization |
| Per-member size | `limits.max_member_bytes` | Declared size + streamed write cap |
| Zip slip | (code) | Resolved path must stay under extract root |
| Symlinks in zip | (code) | Unix symlink entries rejected |
| Special file types | (code) | Non-file/non-dir Unix modes rejected |
| Symlinks on disk | (code) | Walk after extract rejects symlinks |
| Nested archives | `security.reject_nested_archives` | Blocks `.zip`, `.7z`, `.rar`, `.tar.*`, etc. inside the submission |

Implementation: [`scripts/submission_common.py`](../scripts/submission_common.py).

### Extension allowlist

After extract, every file under the source tree must match **`security.allowed_extensions`** in `submissions.yaml` (images, office docs, AV, etc.). Extensionless files and executables (`.exe`, `.html`, `.sh`, …) are rejected. Set `allowed_extensions: []` in config to disable the allowlist (not recommended for external submitters).

### Malware scanning (ClamAV)

1. **`scan_file`** on the `.zip` before extract (with **`ScanArchive yes`** in [`config/clamav/clamd.conf`](../config/clamav/clamd.conf) so nested archive bytes inside the zip are inspected).
2. **`scan_sip_tree`** on every extracted file in [`ingest-folder.py`](../scripts/ingest-folder.py) before BagIt build.

On success, ingest writes a **PREMIS 3** virus-check event at `metadata/premis/virus-check-event.xml` (linked to `package_id` / `package_uuid` in `submission.json`) and refreshes BagIt manifests before Wasabi upload. Every byte of every file is scanned, whatever its size ([`scripts/clamav_full_scan.py`](../scripts/clamav_full_scan.py): files over ClamAV's ~2 GiB object cap are scanned as disc-image members or overlapping raw chunks). The event says `success` only when all bytes were scanned and nothing was found; if clamd reports a limit (`AlertExceedsMax`), the event is `partial` and names the file.

ClamAV recursion limits on the daemon (`MaxRecursion`, `MaxFiles`) align with submission caps; size limits are lifted (see [`config/clamav/README.md`](../config/clamav/README.md)). ClamAV is signature-based; zero-days and non-malware abuse can still pass.

**After changing `clamd.conf`**, recreate the ClamAV container so settings load:

```bash
docker compose up -d --force-recreate clamav
```

### Packaging caps (second line of defense)

Generated game config picks **`max_files`** and **`max_total_bytes`** from [`config/preservation/ingest.yaml`](../config/preservation/ingest.yaml) `defaults` (see [config-contract.md](./config-contract.md)) so `build-bagit-package.py` refuses oversized trees even if zip checks were bypassed. Override only in `submissions.yaml` `preservation` when you need a stricter cap.

### Downstream risk

Ingest does not open files in Omeka or a browser. After **Omeka DIP catalog registration**, staff and public viewers are a separate trust boundary—treat new submissions like any other untrusted user content until reviewed.

### Regression tests

Curated samples (EICAR, Snyk/HEXAQA zip-slip, synthetic allowlist/bomb cases) live under [`fixtures/submissions-security/`](../fixtures/submissions-security/README.md). Run:

```bash
./scripts/test-submission-security-fixtures.sh
```

Re-download upstream zips and regenerate synthetics per that README.

### Operational checklist

- [ ] Known submitter / ticket reference in manifest `submitter` / `notes`
- [ ] Ingest on the preservation host only (`ingest-submission-incoming.sh`)
- [ ] Inspect `failed/` and ledger `status = failed`
- [ ] Keep ClamAV image and virus definitions current
- [ ] Tighten `limits.*` for external partners if needed

## Future

- HTTP upload UI or SFTP-only `incoming` watch (same script, triggered by cron or inotify).
- Ledger columns for `submitter` / `material_type` (today stored in generated game YAML `submission` block).
