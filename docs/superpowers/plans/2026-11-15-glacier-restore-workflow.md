# Glacier Restore Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archivists report a damaged package in the ledger, an admin starts a restore from the Glacier copy with one button, a Windmill job does the slow work, and nothing is written to Wasabi until an admin approves it.

**Architecture:** The ledger only reads and writes two new tables (`problem_report`, `restore_request`). It never holds AWS or Wasabi keys. A Windmill script, `glacier-restore-worker.py`, runs every 15 minutes in the ingest-worker container and moves each open request one step forward. It checks Wasabi first and restores only the files that are wrong. Files go from the restored Glacier copy straight back to Wasabi, with each file's SHA-256 checked on the way, so no machine needs disk space for a restore.

**Tech Stack:** Python 3, boto3 (AWS and Wasabi clients from `glacier_copy_core.py` and `upload_bag_wasabi.py`), PostgreSQL ledger, the ledger's `http.server` app, Windmill scripts and schedules.

**Spec:** the Design section below, agreed with the user on 2026-11-15. The command-line restore it automates is in `docs/restore-from-glacier.md`.

## Design

### Who does what

| Person | Can |
|--------|-----|
| Anyone signed in to the ledger | Report a problem with a package |
| Admin (email listed in `ledger.yaml` under `restore.admins`) | Start a restore, approve or cancel it, close a report |
| Windmill job | Check Wasabi, request restores from AWS, check the restored files, write approved files to Wasabi, run the Wasabi fixity check, add a PREMIS event |

An empty or missing `restore.admins` list means nobody can start or approve a restore.

### A request's states

```
checking ──► not_needed          no file on Wasabi is missing or corrupted
    │   └──► cannot_restore      files are missing or corrupted, but the copy only has older versions of them
    ▼
restoring ──► failed             a restored file doesn't match its recorded SHA-256
    ▼
awaiting_approval ──► cancelled  an admin cancels
    │             └──► expired   the 7 days the restored files stay readable have passed
    ▼ (admin approves)
approved ──► writing ──► done | failed
```

Open states: `checking`, `restoring`, `awaiting_approval`, `approved`, `writing`. A package has at most one open request.

### The check before any restore (the user's requirement)

Before asking AWS for anything, the job labels every file of the package on Wasabi. A bag on Wasabi can be newer than its copy, for example after a new catalog snapshot, and a newer file can still have been overwritten with bad data. So the job checks each file against the bag's own manifests first, and against the copy second.

**Which manifests to trust.** The job uses Wasabi's manifests when Wasabi's `manifest-sha256.txt` matches its entry in Wasabi's `tagmanifest-sha256.txt`. Otherwise one of those two files is corrupted, and the job uses the copy's `sha256` values (read with HEAD, no restore needed) for every file instead, and labels the bad manifest file `corrupted`. When Wasabi's tag manifest matches the one recorded in `package_copy.tagmanifest_sha256`, both sources agree, and the job uses the copy's values. If a manifest is damaged and the bag doesn't look unchanged since the copy (the same files, and every file other than the two manifests matching the copy), no file is restorable, and each bad file gets the note "The bag's manifests on Wasabi are damaged and the bag changed after its last copy, so this file may be newer than the copy. Check it by hand." (Added after the final review.)

**Labels.** Each file gets one label, and the ledger shows it in the request's file table:

| Label | Meaning | Restored? |
|-------|---------|-----------|
| `ok` | The Wasabi file matches the trusted digest, and the copy has the same version | No |
| `newer` | The Wasabi file matches the trusted digest, but the copy has an older version. Wasabi changed after the last copy, which is normal | No |
| `missing` | The bag lists the file, but it isn't on Wasabi | Yes, when the copy's version matches the trusted digest |
| `corrupted` | The Wasabi file doesn't match the trusted digest | Yes, when the copy's version matches the trusted digest |

A `missing` or `corrupted` file whose copy has a different version can't be fixed from the copy. It gets `restorable: false` and the note "The copy only has an older version of this file." The job restores the other files and lists these in the request's message.

**Outcomes.**

- No file is `missing` or `corrupted`: the request ends as `not_needed` with "No file on Wasabi is missing or corrupted. Nothing needs restoring." Nothing is requested from AWS, so it costs nothing.
- Some files are `missing` or `corrupted` and none can be restored: `cannot_restore` with "N files are missing or corrupted, but the copy only has older versions of them. See docs/restore-from-glacier.md."
- Otherwise only the restorable files are restored. The message says, for example, "Restoring 1 corrupted and 1 missing file. 40 files on Wasabi are fine and won't be restored."
- Before writing each approved file, the job checks the Wasabi file again. If it now matches, for example because someone fixed it by hand, the job skips it and notes "already correct on Wasabi, not written."
- The ledger shows a hint on the restore form when the latest Wasabi fixity check passed: "The last Wasabi check, on DATE, passed, so these files may not need a restore. The restore job checks every file first and restores only the ones that are missing or corrupted."

The same rules apply to a dump's disc-contents bag, using the second part of `tagmanifest_sha256`.

### Writing back to Wasabi

- The job reuses `glacier_copy_core.copy_file`, with the restored Glacier object as the source and Wasabi as the target. It completes an upload only when the SHA-256 matches, so a bad read never replaces a Wasabi file. S3 and Wasabi replace an object only when a single PUT or a multipart upload completes.
- After the last file, the job runs `wasabi-fixity-check.py --game-key KEY`. A pass adds a PREMIS `recovery` event to the bag and ends the request as `done`. A failure ends it as `failed`.
- The new PREMIS event changes the tag manifest. The next nightly `glacier_copy` run then copies the package again. That's expected.

### Costs shown on the restore form

The estimate is `GB × tier price + GB × 2 × $0.09`. The tier price is $0.0025/GB for Bulk and $0.02/GB for Standard. The files are read twice, once to check them and once to write them, and downloads cost $0.09/GB after the first 100 GB each month. The form says the estimate is an upper limit for the whole package, because only missing and corrupted files are restored.

## Global Constraints

- AWS: us-east-2 only; the bucket is `hitsave-backup-glacier`; the job uses the existing writer key (`secrets/aws-glacier-copy.yaml`). It has `s3:RestoreObject` and `s3:GetObject` and needs no IAM change.
- The ledger gets no AWS or Wasabi credentials.
- Restored files stay readable for `RESTORE_DAYS = 7` days (`scripts/glacier-restore.py`).
- The new Windmill schedule is committed with `enabled: false`. Turning it on needs the user's OK.
- Applying `schema/014_restore.sql` to the production ledger needs the user's OK, before the ledger deploy that uses it.
- All text people read (ledger pages, docs) follows `docs/` style: plain English per refactoringenglish.com, no colon-then-reveal, no em-dash asides.
- Don't describe saturn as part of the workflow. Windmill runs the job; docs say "the Windmill job".
- Tests run in the `hitsave-glacier-test:local` image: `docker run --rm --entrypoint "" -v "$PWD:/repo:ro" -w /repo -e PYTHONPATH=/repo/scripts hitsave-glacier-test:local pytest -q -p no:cacheprovider <args>`.

## Review Focus

1. **The copy is older than Wasabi and a newer file was then corrupted:** a newer file that matches Wasabi's manifest must be `newer` and left alone, and a corrupted file must be restored only when the copy has the version the manifest lists. Tests: `test_check_labels_newer_files_and_leaves_them_alone`, `test_check_corrupted_file_with_older_copy_is_not_restorable` (Task 2).
2. **A file was fixed by hand between approval and writing:** the job must skip it, not overwrite it. Test: `test_write_skips_file_that_now_matches` (Task 2).
3. **The restore expires before an admin approves:** the request must end `expired` and write nothing. Test: `test_awaiting_approval_expires_after_restore_window` (Task 3).
4. **The job stops halfway through writing** (container killed): the next run must carry on without writing a finished file again. Test: `test_writing_resumes_after_interruption` (Task 3).
5. **A signed-in user who isn't an admin posts to `/restore/request` or `/restore/approve` directly:** the ledger must refuse and change nothing. Test: `test_non_admin_cannot_request_or_approve` (Task 5).

---

### Task 1: Tables for reports and restore requests

**Files:**
- Create: `schema/014_restore.sql`
- Create: `scripts/restore_states.py`
- Test: `tests/test_restore_schema.py`

**Interfaces:**
- Produces: tables `problem_report` and `restore_request`; module `restore_states` with `OPEN = ("checking", "restoring", "awaiting_approval", "approved", "writing")`, `FINISHED = ("not_needed", "cannot_restore", "done", "failed", "cancelled", "expired")`, `TIERS = ("Bulk", "Standard")`.

`problem_report`: `id BIGSERIAL PK`, `game_ingest_id BIGINT NOT NULL REFERENCES game_ingest(id) ON DELETE CASCADE`, `note TEXT NOT NULL`, `reported_by TEXT NOT NULL`, `reported_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `closed_by TEXT`, `closed_at TIMESTAMPTZ`, `close_note TEXT`.

`restore_request`: `id BIGSERIAL PK`, `game_ingest_id` (as above), `problem_report_id BIGINT REFERENCES problem_report(id)`, `state TEXT NOT NULL DEFAULT 'checking'` with a CHECK listing all eleven states, `tier TEXT NOT NULL` with CHECK in `TIERS`, `reason TEXT NOT NULL`, `requested_by TEXT NOT NULL`, `requested_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `approved_by TEXT`, `approved_at TIMESTAMPTZ`, `files JSONB NOT NULL DEFAULT '[]'`, `message TEXT`, `restore_expires_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `finished_at TIMESTAMPTZ`. Add `CREATE UNIQUE INDEX restore_request_one_open ON restore_request (game_ingest_id) WHERE state IN (<OPEN>)`. Head the file with a comment block in the style of `schema/013_package_copy.sql` that explains each column and the states.

Each entry in `files` is `{"bag": "<wasabi bucket>/<bag prefix>/", "rel": "data/x.iso", "size": 123, "sha256": "...", "verdict": "ok|newer|missing|corrupted", "restorable": true, "written": "yes|skipped|no", "note": "..."}`.

- [ ] **Step 1: Write the failing test**

```python
def test_schema_states_match_restore_states():
    sql = (ROOT / "schema" / "014_restore.sql").read_text()
    for state in restore_states.OPEN + restore_states.FINISHED:
        assert f"'{state}'" in sql
    assert len(set(restore_states.OPEN + restore_states.FINISHED)) == 11
    index = sql.split("restore_request_one_open", 1)[1].split(";", 1)[0]
    assert all(f"'{s}'" in index for s in restore_states.OPEN)
    assert not any(f"'{s}'" in index for s in restore_states.FINISHED)
```

- [ ] **Step 2: Run it.** Expected: FAIL, `ModuleNotFoundError: restore_states`.
- [ ] **Step 3: Write `scripts/restore_states.py` and `schema/014_restore.sql`.**
- [ ] **Step 4: Run it.** Expected: PASS. Also run `psql` against a throwaway `postgres:16` container with `001`–`014` applied in order, to check that the SQL parses: `docker run --rm -d --name rs-pg -e POSTGRES_PASSWORD=x postgres:16`, then `cat schema/0*.sql | docker exec -i rs-pg psql -U postgres -v ON_ERROR_STOP=1`. Expected: no errors. Then `docker rm -f rs-pg`.
- [ ] **Step 5: Commit** `schema/014_restore.sql scripts/restore_states.py tests/test_restore_schema.py`: "Restore: tables for problem reports and restore requests".

### Task 2: Check, restore and write-back functions

**Files:**
- Create: `scripts/glacier_restore_core.py`
- Modify: `scripts/glacier_copy_core.py` (`copy_file`, `_check_stored`: target options)
- Modify: `scripts/glacier-restore.py` (import `is_ready`, `RESTORE_DAYS` and `request` from the new module instead of defining them)
- Modify: `tests/glacier_fakes.py` (an optional `restore_expiry` string per FakeS3, used in the `Restore` header)
- Test: `tests/test_glacier_restore_core.py`

**Interfaces:**
- Consumes: `glacier_copy_core.copy_file`, `expected_digests`, `list_source`, `TAGMANIFEST`; `glacier-restore.py`'s `is_ready`, `request` (moved here).
- Produces:
  - `@dataclass(frozen=True) class Target: storage_class: str | None; crc64nvme: bool`; `DEEP_ARCHIVE = Target("DEEP_ARCHIVE", True)`; `WASABI = Target(None, False)` in `glacier_copy_core`. `copy_file(..., target: Target = DEEP_ARCHIVE)`. With `WASABI`, `copy_file` sends no `StorageClass` and no checksum arguments, keeps `Metadata={"sha256": ...}`, and `_check_stored` checks size and `sha256` metadata only.
  - `@dataclass class FileCheck: bag: str; rel: str; size: int; sha256: str; verdict: str; restorable: bool; note: str = ""`. `sha256` is the copy's digest (what a restore would write). `verdict` is `ok`, `newer`, `missing` or `corrupted`, as in the Design section. `to_json() -> dict`, `from_json(d) -> FileCheck`.
  - `check_bag(wasabi_s3, wasabi_bucket: str, bag_prefix: str, aws_s3, aws_bucket: str, recorded_tagmanifest_sha256: str) -> list[FileCheck]`. It picks the trusted digests by the rules in the Design section, lists the copy under `f"{wasabi_bucket}/{bag_prefix}/"` and Wasabi under `bag_prefix`, reads each copy object's `sha256` with HEAD, hashes each Wasabi file, and labels the union of files the trusted manifests list and files in the copy. A file only on Wasabi and in no manifest isn't labelled.
  - `summary_message(files: list[FileCheck]) -> tuple[str, str]`: (next state, message), using the three outcomes in the Design section. Next state is `not_needed`, `cannot_restore` or `restoring`.
  - `restore_progress(aws_s3, aws_bucket: str, files: list[FileCheck]) -> tuple[int, int, datetime | None]`: (ready, waiting, earliest expiry parsed from `expiry-date` in the `Restore` header).
  - `verify_restored(aws_s3, aws_bucket: str, files: list[FileCheck]) -> list[str]`: reads each restored object, returns one problem per SHA-256 mismatch.
  - `write_back(aws_s3, aws_bucket: str, wasabi_s3, wasabi_bucket: str, f: FileCheck) -> tuple[str, str]`: returns (`"yes"` | `"skipped"` | `"no"`, note). It hashes the Wasabi file first: if it matches `f.sha256`, it returns `("skipped", "already correct on Wasabi, not written")`. Otherwise it calls `copy_file(aws → wasabi, expected_sha256=f.sha256, target=WASABI)`.
  - The message texts are exactly those in the Design section's Outcomes list. Use the singular "file" when a count is 1.

The copy key for a file is `f"{f.bag}{f.rel}"`, where `f.bag` is `"<wasabi bucket>/<bag prefix>/"`. The Wasabi key is the same without the first path part.

- [ ] **Step 1: Write the failing tests.** Build bags with `tests/test_glacier_copy_job.make_bag` and copy them with `copy_package` so that the copy has real `sha256` metadata, then damage Wasabi:

```python
def test_check_all_ok_when_wasabi_matches_copy(): ...
    assert {f.verdict for f in check_bag(...)} == {"ok"}

def test_check_finds_corrupted_and_missing_files(): ...
    # overwrite data/disc.iso on Wasabi with other bytes, delete data/readme.txt
    assert {(f.rel, f.verdict, f.restorable) for f in files if f.verdict != "ok"} == {
        ("data/disc.iso", "corrupted", True), ("data/readme.txt", "missing", True)}

def test_check_labels_newer_files_and_leaves_them_alone(): ...
    # make_bag(src, root, note=b"v2") after the copy: a changed tag file, a new tag manifest
    changed = {f.rel: f for f in files if f.verdict != "ok"}
    assert {f.verdict for f in changed.values()} == {"newer"}
    assert summary_message(files)[0] == "not_needed"

def test_check_corrupted_file_with_older_copy_is_not_restorable(): ...
    # bag changes to v2 after the copy, then the changed tag file is overwritten with junk
    bad = [f for f in files if f.verdict == "corrupted"]
    assert len(bad) == 1 and bad[0].restorable is False
    assert bad[0].note == "The copy only has an older version of this file."
    assert summary_message(files)[0] == "cannot_restore"

def test_check_uses_copy_digests_when_payload_manifest_is_corrupted(): ...
    # overwrite manifest-sha256.txt on Wasabi with junk
    assert {(f.rel, f.verdict) for f in files if f.verdict != "ok"} == {("manifest-sha256.txt", "corrupted")}

def test_summary_messages(): ...
    assert summary_message([ok] * 40 + [corrupted, missing]) == (
        "restoring", "Restoring 1 corrupted and 1 missing file. 40 files on Wasabi are fine and won't be restored.")
    assert summary_message([ok, newer]) == (
        "not_needed", "No file on Wasabi is missing or corrupted. Nothing needs restoring.")

def test_restore_progress_counts_ready_and_reads_expiry(): ...
    assert restore_progress(...) == (1, 1, datetime(2026, 11, 27, tzinfo=timezone.utc))

def test_verify_restored_reports_a_mismatch(): ...

def test_write_back_replaces_damaged_file_with_verified_bytes(): ...
    assert write_back(...) == ("yes", "")
    assert wasabi.objects()[key].data == PAYLOAD
    assert "StorageClass" not in wasabi.puts[-1] and "ChecksumCRC64NVME" not in wasabi.puts[-1]

def test_write_skips_file_that_now_matches(): ...
    assert write_back(...) == ("skipped", "already correct on Wasabi, not written")
    assert wasabi.puts == []

def test_write_back_never_stores_wrong_bytes(): ...
    # change the restored copy's bytes but not its sha256 metadata
    assert write_back(...)[0] == "no"
    assert wasabi.objects()[key].data == DAMAGED   # the old object is still there
```

- [ ] **Step 2: Run** `pytest tests/test_glacier_restore_core.py`. Expected: FAIL, `ModuleNotFoundError: glacier_restore_core`.
- [ ] **Step 3: Add `Target` to `glacier_copy_core.py`, then write `glacier_restore_core.py`.** Move `is_ready`, `request` and `RESTORE_DAYS` from `glacier-restore.py` and import them back there.
- [ ] **Step 4: Run** the new tests, then `tests/test_glacier_copy_core.py tests/test_glacier_restore.py tests/test_glacier_copy_job.py`. Expected: all PASS. The copy job still sends `StorageClass="DEEP_ARCHIVE"` and CRC64NVME.
- [ ] **Step 5: Commit**: "Restore: check Wasabi against the copy and write verified files back".

### Task 3: The restore worker and its Windmill job

**Files:**
- Create: `scripts/glacier-restore-worker.py`
- Create: `windmill_ops/wmill/f/hitsave/glacier_restore_worker.sh`, `.script.yaml`, `.schedule.yaml`
- Test: `tests/test_glacier_restore_worker.py`

**Interfaces:**
- Consumes: Task 1's tables and `restore_states`; Task 2's functions; `glacier_copy_core.prefixes_of`, `make_aws_client`, `connect`; `upload_bag_wasabi.load_wasabi_settings`, `make_s3_client`; `premis_events.PremisEvent`, `stage_event`.
- Produces: `run(argv, *, ledger, aws_s3, aws_bucket, wasabi_s3, bucket_for, fixity_check, now) -> dict` with counts per new state. `ledger` has `open_requests() -> list[dict]` (joined with `game_ingest` and `package_copy`, ordered by id), `save(request_id, **fields)` (always sets `updated_at`, and `finished_at` when the state is finished) and `close_report(report_id, by, note)`. `fixity_check(game_key) -> bool` runs `python /app/scripts/wasabi-fixity-check.py --game-key KEY` and returns whether it exited 0. `now() -> datetime`.

`step(req)` per state:

| State | What the job does |
|-------|-------------------|
| `checking` | `check_bag` for each prefix in `prefixes_of(row)`, using the matching comma-separated part of `tagmanifest_sha256`. Save `files`, then apply `summary_message`. For `restoring`, first `request` the restorable files with the request's tier. |
| `restoring` | `restore_progress`. Still waiting → no change. All ready → `verify_restored`; problems → `failed` with the problems as the message; else save `restore_expires_at` → `awaiting_approval`. |
| `awaiting_approval` | `now() >= restore_expires_at` → `expired` with "The restored files stopped being readable before anyone approved them. Start a new restore." Otherwise nothing. |
| `approved`, `writing` | Expired → `expired`. Else set `writing`, then `write_back` each restorable file whose `written` isn't `yes` or `skipped`, saving `files` after each one. Any `no` → `failed`. After the last file: `fixity_check(game_key)`; pass → stage a PREMIS event (`event_type="recovery"`, `detail="Restored N files from the AWS Glacier Deep Archive copy (restore request ID)"`, `program="glacier-restore-worker.py"`, package id and UUID read from the bag's `metadata/submission.json`, as `check-integrity-wasabi.py` does), upload the event and then the tag manifest, close the linked report with "Restored from the Glacier copy" → `done`; fail → `failed` with "The Wasabi fixity check failed after the restore. See the fixity row on the ledger page." |

An access error (`is_fatal`) stops the run with exit code 2 and changes nothing. Any other error on one request saves `message` and moves on to the next request without changing that request's state.

- [ ] **Step 1: Write the failing tests** with a `FakeLedger` holding request dicts, the FakeS3s from Task 2 and `fixity_check=lambda key: True`:

```python
def test_checking_all_ok_ends_not_needed_without_restore_requests(): ...
    assert req["state"] == "not_needed" and aws.restores == []
    assert req["message"] == "No file on Wasabi is missing or corrupted. Nothing needs restoring."

def test_checking_requests_only_corrupted_files(): ...
    assert req["state"] == "restoring" and [r["Key"] for r in aws.restores] == [copy_key("data/disc.iso")]
    assert req["message"].startswith("Restoring 1 corrupted file.")

def test_checking_cannot_restore_requests_nothing(): ...

def test_restoring_waits_then_moves_to_awaiting_approval(): ...

def test_awaiting_approval_expires_after_restore_window(): ...
    assert req["state"] == "expired" and wasabi.puts == []

def test_awaiting_approval_never_writes(): ...

def test_approved_writes_runs_fixity_and_adds_recovery_event(): ...
    assert req["state"] == "done"
    assert any("recovery" in o.data.decode() for k, o in wasabi.objects().items() if "/metadata/" in k)
    assert ledger.closed_reports == [(7, "glacier-restore-worker", "Restored from the Glacier copy")]

def test_writing_resumes_after_interruption(): ...
    # files[0]["written"] = "yes" and state "writing"; count wasabi puts for files[0]
    assert puts_for(files[0]) == 0 and req["state"] == "done"

def test_failed_fixity_check_ends_failed(): ...

def test_access_denied_stops_without_changing_state(): ...
```

- [ ] **Step 2: Run** `pytest tests/test_glacier_restore_worker.py`. Expected: FAIL, missing module.
- [ ] **Step 3: Write `scripts/glacier-restore-worker.py`** with `run`, `step`, a SQL `Ledger` class (mirroring `glacier-copy-check.py`'s `Ledger`), and `main()` that builds the clients and prints the result as JSON.
- [ ] **Step 4: Run** the worker tests, then the whole suite. Expected: all PASS.
- [ ] **Step 5: Add the Windmill job.** `glacier_restore_worker.sh` follows `glacier_copy.sh` (bootstrap, `archiver_compose run ... -v "$ARCHIVER_HOST_PATH/scripts:/app/scripts:ro" --entrypoint python ingest-worker /app/scripts/glacier-restore-worker.py`). Schedule `0 */15 * * * *`, `no_flow_overlap: true`, `enabled: false`. Check the cron line with the command in `windmill_ops/README.md` before the PR.
- [ ] **Step 6: Commit**: "Restore: Windmill job that moves restore requests forward".

### Task 4: Report a problem (everyone) and the restores page

**Files:**
- Create: `ledger/restore_panel.py`
- Modify: `ledger/item_page.py` (render the panel under the Second copy line), `ledger/app.py` (routes, nav), `ledger/site_ui.py` (nav entry "Restores")
- Test: `tests/test_ledger_restore_panel.py`

**Interfaces:**
- Consumes: Task 1's tables and `restore_states`.
- Produces:
  - `fetch_restore_state(connect, game_ingest_id: int) -> dict` with `reports` (open, newest first), `request` (newest request or `None`) and `last_wasabi_check` (`{"outcome", "checked_at", "detail"}` of the newest `fixity_run` whose detail starts with `wasabi:`, or `None`). `scripts/wasabi-fixity-check.py` writes that prefix as `DETAIL_PREFIX`; the hyphenated script can't be imported, so copy the value and add a test that reads it from the script's source.
  - `render_restore_panel(state: dict, *, game_key: str, is_admin: bool, has_copy: bool, total_bytes: int) -> str`.
  - `handle_restore_post(path: str, form: dict[str, list[str]], user: str, *, connect, admins: set[str]) -> tuple[str, str, str]` returning (game_key, notice kind, notice message). In this task it handles `/restore/report` only.
  - `render_restores_page(*, requests: list[dict], reports: list[dict], status_config: dict) -> bytes`, served at `GET /restores`: open reports, then open requests, then the last 20 finished ones.

`/restore/report` needs a signed-in user and a non-empty note of at most 2,000 characters. It saves a `problem_report` row and redirects to the item page with "Thanks. An admin will look at this package." The panel lists open reports with who reported them and when.

- [ ] **Step 1: Write the failing tests**:

```python
def test_report_saves_note_and_user(): ...
    assert handle_restore_post("/restore/report", {"game_key": ["pkg"], "note": ["disc.iso won't open"]},
                               "a@example.org", connect=fake, admins=set()) == (
        "pkg", "ok", "Thanks. An admin will look at this package.")
    assert fake.reports[-1]["reported_by"] == "a@example.org"

def test_report_needs_a_note_and_a_user(): ...

def test_panel_lists_open_reports(): ...

def test_restores_page_lists_open_reports_and_requests(): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL, missing module.
- [ ] **Step 3: Write `ledger/restore_panel.py`** and wire `POST /restore/report` and `GET /restores` in `app.py`. POST redirects with `with_notice` like the existing visibility actions.
- [ ] **Step 4: Run** the new tests and `tests/test_ledger_*.py`. Expected: all PASS.
- [ ] **Step 5: Commit**: "Ledger: report a problem with a package, and a Restores page".

### Task 5: Start, approve and cancel a restore (admins)

**Files:**
- Modify: `ledger/restore_panel.py`, `ledger/app.py` (read `restore.admins` from `ledger.yaml`), `config/ledger.yaml.example` (an empty `restore: {admins: []}` with a comment)
- Test: `tests/test_ledger_restore_panel.py`

**Interfaces:**
- Consumes: Task 4's functions.
- Produces: `handle_restore_post` also handles `/restore/request` (fields `game_key`, `tier`, `reason`, optional `report_id`), `/restore/approve` and `/restore/cancel` (field `request_id`), and `/restore/close-report` (fields `report_id`, `note`). `estimate_usd(total_bytes: int, tier: str) -> float` = `GB × (0.0025 | 0.02) + GB × 2 × 0.09`.

Rules:
- Every admin action checks `user in admins`. Otherwise it returns `("…", "error", "Only admins can do this.")` and changes nothing.
- `/restore/request` refuses a package with no `package_copy` row ("This package has no copy in Glacier yet.") or with an open request ("A restore is already open for this package."). It inserts with state `checking`. An `IntegrityError` from the unique index gives the same "already open" message.
- `/restore/approve` changes the state only from `awaiting_approval` to `approved`, with `approved_by` and `approved_at`, in one `UPDATE ... WHERE state = 'awaiting_approval'`. If no row changed, it says "This restore isn't waiting for approval any more."
- `/restore/cancel` works from `checking`, `restoring` and `awaiting_approval`, never from `approved` or `writing`.
- The panel, for admins, shows the request form when no request is open: tier select (Bulk preselected), reason, both cost estimates, and the Wasabi-check hint from the Design section when `last_wasabi_check.outcome == "pass"`. When the latest check failed, it shows that check's date and detail instead. When a request is open, it shows its state, `message`, and the per-file table (file, label, restorable, written, note). The labels read "fine", "newer on Wasabi", "missing" and "corrupted". It shows **Approve** and **Cancel** at `awaiting_approval`, with "Approving writes N files to Wasabi."

- [ ] **Step 1: Write the failing tests**:

```python
def test_non_admin_cannot_request_or_approve(): ...
    for path, form in [("/restore/request", REQ), ("/restore/approve", {"request_id": ["1"]})]:
        assert handle_restore_post(path, form, "someone@example.org", connect=fake, admins={"admin@example.org"})[1] == "error"
    assert fake.requests == [OPEN_AWAITING]   # unchanged

def test_request_refused_without_copy_or_with_open_request(): ...

def test_approve_only_from_awaiting_approval(): ...

def test_cancel_refused_while_writing(): ...

def test_form_shows_hint_when_last_wasabi_check_passed(): ...
    assert "The last Wasabi check, on 2026-11-14, passed, so these files may not need a restore." in html

def test_estimate_for_a_4_gb_dvd(): ...
    assert round(estimate_usd(4 * 10**9, "Bulk"), 2) == 0.73
    assert round(estimate_usd(4 * 10**9, "Standard"), 2) == 0.80

def test_awaiting_approval_shows_file_table_and_buttons(): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Extend `restore_panel.py` and the routes.** Log each action with `audit("restore-<action>", user, game_key=..., request_id=...)`.
- [ ] **Step 4: Run** the new tests and the whole suite. Expected: all PASS.
- [ ] **Step 5: Commit**: "Ledger: admins start, approve and cancel Glacier restores".

### Task 6: Docs

**Files:**
- Modify: `docs/restore-from-glacier.md`, `docs/archivist-guide.md`, `docs/glacier-copy.md`, `windmill_ops/README.md`, `AGENTS.md`

- [ ] **Step 1: `docs/restore-from-glacier.md`:** a new first section, "Restore from the ledger", covering the buttons, states, the check before restoring, approval and costs. Keep the command-line steps below it for when the ledger is down, the request ends as `cannot_restore`, or all of Wasabi is lost.
- [ ] **Step 2: `docs/archivist-guide.md`:** replace "tell an admin" in "If a preserved file is damaged or lost" with the **Report a problem** button. Keep the advice not to change anything on Wasabi.
- [ ] **Step 3: `windmill_ops/README.md` and `AGENTS.md`:** list `glacier_restore_worker` and its schedule; `docs/glacier-copy.md`: link to the new section.
- [ ] **Step 4: Commit**: "Docs: restore from the Glacier copy through the ledger".

## Rollout (each step needs the user's OK)

1. Merge on green CI.
2. Apply `schema/014_restore.sql` to the production ledger.
3. Add the admins' emails to `restore.admins` in the private `ledger.yaml`. Deploy the ledger.
4. Enable the `glacier_restore_worker` schedule.
5. Test with one press kit: damage nothing, start a restore, and expect `not_needed`. This also tests the ledger, the job and the check, at no AWS cost.
6. The first real repair is the first test of the write step outside the fakes. Do it on a press kit, where a mistake costs little, and watch each state.
