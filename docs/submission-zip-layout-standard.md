# Submission zip layout standard (planned)

**Status:** design target — not fully implemented in `ingest-submission.py` yet. Today we only **unwrap a single top-level folder**; loose files at the zip root are ingested as-is if they pass the extension allowlist.

Goal: **standardize on zip as the archivist delivery format** and decide unambiguously what the **game package** is before E-ARK ingest.

## Terms

| Term | Meaning |
|------|---------|
| **Archive base name** | Zip filename without `.zip` (e.g. `Pac-Man_CDX` from `Pac-Man_CDX.zip`) |
| **Game folder** | Directory whose contents become `source_game_folder` for AIP/DIP |
| **Game name** | Human title / slug source — manifest, folder name, or archive base name |
| **Quarantine** | Reject with explicit reason; zip → `failed/` (or future `quarantine/`); no ingest |

## Resolution order (planned)

After safe extract under `workspace/<archive>/extracted/`:

```
1. hitsave-submission.yaml (if present)
   → game_key, omeka_item_title override (already supported)

2. Single top-level directory, no loose files at extract root
   → game folder = that directory
   → game name default = directory name (normalized for display / slug)

3. Else: loose files and/or multiple top-level directories
   → If EVERYTHING at extract root is allowlisted press material AND
     archive base name looks like a valid game slug
   → Re-stage into workspace/.../game/<archive_base_name>/
   → game folder = that new directory
   → game name default = archive base name (normalized)

4. Else: quarantine
   → Example: bob.zip containing only run.exe at root
   → Reason: layout_unrecognized or disallowed_content_at_root
```

Step 3 is the new behavior you described: **`MyGame.zip`** with `screenshot.jpg` + `readme.txt` at the root (no folder) still ingests as game **`MyGame`**.

Step 4 is stricter than today: a lone **`.exe`** (or other disallowed type) at root → **quarantine**, not a generic allowlist error buried in logs.

## Examples

| Zip | Contents | Outcome |
|-----|----------|---------|
| `Topatoi.zip` | `Topatoi/*.jpg` | **OK** — rule 2, folder name = game |
| `Pac-Man_CDX.zip` | `readme.txt`, `box.jpg` at root | **OK** — rule 3, restage under `Pac-Man_CDX/` |
| `Pac-Man_CDX.zip` | `Pac-Man_CDX/readme.txt` | **OK** — rule 2 |
| `bob.zip` | `setup.exe` only | **Quarantine** — rule 4 |
| `bob.zip` | `notes.txt` only | **OK** — rule 3 if `.txt` allowed and `bob` is valid slug |
| `materials.zip` | `game_a/`, `game_b/` | **Quarantine** — multiple roots (unless batch manifest defines splits; out of scope v1) |

## Valid “game name” from filename (planned rules)

Use archive base name when restaging (rule 3):

- Strip `.zip`; normalize display title via existing `normalize_display_title`.
- **Slug / game_key:** reuse `slug_game_key(archive_base)` or manifest `game_key` when set.
- Reject archive names that are too short, generic, or placeholder, e.g. `materials`, `files`, `download`, `bob` (configurable blocklist in `submissions.yaml`).
- Optional future: fuzzy match archive base against Moby / internal catalog before ingest.

## Quarantine vs failed

| Today | Planned |
|-------|---------|
| Any error → `failed/` + `error.txt` | **Quarantine** = layout/content policy (operator review, maybe return to archivist) |
| | **Failed** = virus, zip bomb, I/O, E-ARK/Wasabi errors |

Config sketch (not wired yet):

```yaml
layout:
  mode: standard   # standard | legacy (today)
  restage_loose_files: true
  generic_archive_names:
    - materials
    - files
    - download
    - archive
    - bob
  quarantine_subdir: quarantine   # optional separate dir under submissions root
```

## Archivist guidance (to publish with the standard)

1. **Preferred:** one zip per game, **one top-level folder** named like the game: `GameName/...`.
2. **Acceptable:** zip named `GameName.zip` with all files at the root (no folder).
3. Include optional **`hitsave-submission.yaml`** for stable `game_key` and Omeka title.
4. Do not send executables, installers, or nested zips; press images/docs only (see allowlist in `submissions.yaml`).

## Implementation checklist (when you pick this up)

- [ ] `resolve_game_package(extract_root, archive_stem, config) -> GamePackage | QuarantineReason` in `submission_common.py`
- [ ] Restage step: copy/move loose allowlisted files into `.../game/<stem>/`
- [ ] Quarantine path + ledger status `quarantine` (schema migration) or structured `error_message` prefix
- [ ] Fixture zips: loose-jpg ok, loose-exe quarantine, multi-root quarantine
- [ ] Extend `test-submission-security-fixtures.sh` or add `test-submission-layout-fixtures.sh`

## Relation to current code

| Behavior | Today |
|----------|--------|
| Single top-level folder | `resolve_source_folder()` |
| Zip stem → game_key | `slug_game_key(zip_path.stem)` if no manifest |
| Loose files at root | Used as `source_game_folder` if allowlist passes |
| `.exe` at root | **Reject** (allowlist), zip → `failed/` |

See also: [portable-submissions.md](./portable-submissions.md).
