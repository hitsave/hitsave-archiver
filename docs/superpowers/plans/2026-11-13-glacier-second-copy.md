# Second copy in S3 Glacier Deep Archive: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every completed package has a second, verified copy in S3 Glacier Deep Archive, and the ledger records where it is and when it was last checked.

**Architecture:** A nightly Windmill job streams each package from Wasabi to one S3 bucket in us-east-2, file by file, with the same keys as on Wasabi. It checks each file's SHA-256 against the bag manifest while it streams, lets S3 check a full-file CRC64NVME on arrival, and records the result in a new ledger table. A monthly job compares what S3 holds with the bag manifests without restoring anything. A restore script gets files back for repairs and restore tests.

**Tech Stack:** Python 3 and boto3 (with `awscrt`) in the ingest-worker image, PostgreSQL ledger, Windmill on saturn, CloudFormation for the AWS resources.

**Spec:** The Glacier step of the storage plan at https://claude.ai/artifact/3vjx5zzBi87pg8T76e7Ly7 (2026-11-06), and step 1 of the Notion page "HitSave Preservation Levels". The move to IDrive e2 now comes later, so this plan copies from Wasabi.

## What we're copying

Measured on 2026-11-13 by listing every Wasabi bucket:

| Bucket | Objects | Size | In scope |
| --- | ---: | ---: | --- |
| `hitsave-dumps` (dump bags and disc-contents bags) | 8,840 | 1.088 TB | Yes |
| `hitsave-press-material` | 124,835 | 0.126 TB | Yes |
| `hitsave-other`, `hitsave-analog` | 0 | 0 | Yes, once they hold packages |
| `hitsave-incoming` (inbox, `processed/`, press kits waiting for ingest) | 1,336,320 | 5.059 TB | No. The originals are on the on-site share, and each press kit gets a copy when it's ingested. |
| **Total in scope** | **133,675** | **1.214 TB** | |

Most press-material files are small: 80,459 are under 128 KB.

## Costs

AWS list prices for us-east-2, read from the AWS Price List API on 2026-11-13:

| Item | Price | Our cost |
| --- | --- | ---: |
| Storage | $0.00099 per GB-month | $1.20 per month for 1.214 TB |
| Upload requests | $0.05 per 1,000 files | $6.68 once for the backfill |
| Per-file overhead (40 KB per file, partly billed as S3 Standard) | | under $0.05 per month |
| Monthly check (one HEAD request per file) | | about $0.05 per month |
| Minimum storage time | 180 days per file version | |
| Full restore of everything (bulk retrieval, then download) | $0.0025 per GB, then $0.09 per GB after the first free 100 GB | about $107 |

When the 8 TB of press material from `hitsave-incoming` is ingested, storage grows to about $9 per month, and its 1.3 million files cost about $67 to upload once. The TechSoup credits cover all of this many times over.

Copying out of Wasabi is free while monthly downloads stay below the amount stored (about 6.3 TB today), so the 1.2 TB backfill doesn't need to be spread over several months.

## Design decisions

1. **One file in S3 for each file on Wasabi, with the same key**, in one bucket: `s3://<copy bucket>/<wasabi bucket>/<key>`. A restore is a plain copy back. When a bag changes, the job uploads only the files that changed. Packing each package into one tar file would save nothing that matters at our file counts and would make partial restores and incremental updates harder.
2. **Upload straight to the `DEEP_ARCHIVE` storage class.** We don't need a lifecycle rule to move files between classes.
3. **Verify while copying, so we never need a restore to trust a copy:**
   - The job computes each file's SHA-256 while it streams the file from Wasabi, and compares it with the bag's `manifest-sha256.txt` or `tagmanifest-sha256.txt`. If it differs, the job aborts the upload and records a failure, because the file on Wasabi is damaged.
   - The job also computes a full-file CRC64NVME and sends it with the upload. S3 rejects the upload if the bytes it received don't match. CRC64NVME is the only checksum S3 can check over a whole multipart file; SHA-256 works only per part (AWS docs, "Checking object integrity for data uploads").
   - The job stores the SHA-256 in the file's metadata (`x-amz-meta-sha256`). A HEAD request returns the metadata and the CRC64NVME for archived files without a restore, so the monthly check can compare them with the manifest.
4. **Versioning and Object Lock in governance mode, with 365 days' retention.** No one can delete or overwrite a stored version for a year, even with the job's access key. A file that changes or disappears on Wasabi gets a new current version, or a delete marker, in S3, and the old version stays for a year. A lifecycle rule then expires old versions. The lock never blocks a new version, so changes to a bag are copied the same night. Deep Archive bills every version for at least 180 days anyway, so the second half-year costs only the storage of replaced files: fractions of a cent for tag-file edits, about $0.05 a month for a rebuilt 50 GB bag. Governance mode, unlike compliance mode, still lets the project owner remove a version that was uploaded by mistake.
5. **The job's access key can add files but never destroy them.** The IAM user gets `s3:PutObject`, `s3:GetObject`, `s3:GetObjectAttributes`, `s3:ListBucket`, `s3:ListBucketVersions`, `s3:DeleteObject` (which only adds a delete marker in a versioned bucket), `s3:RestoreObject`, `s3:AbortMultipartUpload` and `s3:ListMultipartUploadParts`, on this bucket only. It doesn't get `s3:DeleteObjectVersion`, `s3:BypassGovernanceRetention` or any bucket-settings permission. The paid plan allows IAM users and access keys; the managed policies only block groups, login profiles and the `managed/` roles.
6. **The job runs on saturn and streams through it.** Nothing is written to disk. A copy machine in AWS would avoid saturn's connection, but it adds a server to manage, and saturn has a 1 Gbit line, so 1.2 TB fits in a few nights.
7. **The ledger tracks copies in a new `package_copy` table.** Checks of the copy go into the existing `fixity_run` table, with detail text starting with `glacier:`, the same way the Wasabi check uses `wasabi:`. The ledger's item page and Health page show the copy's state.
8. **A copy is up to date when its recorded `tagmanifest-sha256.txt` digest equals the current one on Wasabi.** Every change to a bag rewrites its tag manifest, so this catches catalog snapshots, PREMIS events and rebuilt bags. If the tag manifest changes while the job copies a package, the job records nothing and tries again the next night, as the Wasabi check does since #170.
9. **The source is a setting.** The job reads the source endpoint and buckets from `ingest.yaml`, so the later move to IDrive e2 doesn't change it.

## Decisions

Jonas decided these on 2026-11-14:

- **Press kits in `hitsave-incoming`** aren't copied. The originals are on the on-site press-material share.
- **Object Lock** keeps every version for 365 days in governance mode.
- **Saturn's line** is 1 Gbit. The Wasabi check reads at about 17 MB/s per file, so a 300 GB batch takes about five hours.
- **The bucket** is `hitsave-backup-glacier`. Bucket names are global, so we'll find out whether it's free when we create the stack.

Still open: Jonas sets a spend limit in AWS Settings, for example $25 per month. Only the project owner can set one, and it stops a runaway job from spending the credits.

## Global Constraints

- All AWS resources are in us-east-2. The project can't create resources in other Regions.
- The bucket blocks all public access and encrypts with SSE-S3.
- No credentials in the public repo. The access key lives in private config: `hitsave-archiver-config/secrets/aws-glacier-copy.yaml`.
- Scripts that write anything run in report-only mode unless given `--execute`.
- Backfill batches, deploying the CloudFormation stack, creating the access key and turning on schedules each need Jonas's OK.
- Nothing in this plan deletes or changes anything on Wasabi. Repairs from the copy are a separate manual step that also needs an OK.
- Writing style for docs and PRs follows `docs-writing-style` (refactoringenglish.com).

## Review Focus

1. **A file larger than 5 GB** (32 dump files are): the multipart path must send the full-file CRC64NVME and complete the upload only after the SHA-256 matches. Covered in Task 3.
2. **A bag that changes during its copy** (the catalog-snapshot job runs at 05:30 UTC): the job must record nothing and leave the package due for the next night. Covered in Task 4.
3. **A file renamed or removed on Wasabi** (as the payload-folder rename in #121 did): the copy must end with the same current keys as Wasabi, and the old versions must stay. Covered in Task 3.
4. **The tag manifest itself has no digest in any manifest:** the job must still copy it and record its SHA-256, without treating "no expected digest" as a failure. Covered in Task 3.
5. **An expired or wrong access key:** the job must fail loudly in Windmill and record nothing, instead of marking packages as failed. Covered in Task 4.

---

### Task 1: AWS resources as a CloudFormation template

**Files:**
- Create: `deploy/aws/glacier-copy.yaml`
- Test: `tests/test_glacier_copy_template.py`

**Interfaces:**
- Produces: stack outputs `BucketName` and `WriterUserName`. Parameters `BucketName` (default `hitsave-backup-glacier`) and `RetentionDays` (default 365).

- [ ] **Step 1: Write the failing test.** Load the template with `yaml` (handle CloudFormation tags with a loader that accepts unknown tags) and assert:

```python
def test_bucket_locks_versions_and_blocks_public_access():
    b = resources_of_type("AWS::S3::Bucket")[0]["Properties"]
    assert b["ObjectLockEnabled"] is True
    assert b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Mode"] == "GOVERNANCE"
    assert b["VersioningConfiguration"]["Status"] == "Enabled"
    assert all(b["PublicAccessBlockConfiguration"][k] for k in
               ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"))

def test_lifecycle_aborts_uploads_and_expires_old_versions():
    rules = {r["Id"]: r for r in bucket_props()["LifecycleConfiguration"]["Rules"]}
    assert rules["abort-incomplete-uploads"]["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 7
    assert "NoncurrentVersionExpiration" in rules["expire-old-versions"]

def test_writer_cannot_destroy_versions_or_change_the_bucket():
    actions = writer_policy_actions()
    assert "s3:DeleteObjectVersion" not in actions
    assert "s3:BypassGovernanceRetention" not in actions
    assert not any(a.startswith("s3:PutBucket") or a == "s3:*" for a in actions)
```

- [ ] **Step 2: Run it.** `pytest tests/test_glacier_copy_template.py -v`. Expected: FAIL, template missing.
- [ ] **Step 3: Write the template.** The bucket, the writer IAM user, and its inline policy with exactly the actions in design decision 5. Set `NoncurrentVersionExpiration` to `RetentionDays + 1` days. The template creates no access key.
- [ ] **Step 4: Run the test and the linters.** `pytest tests/test_glacier_copy_template.py -v` passes, and `cfn-lint deploy/aws/glacier-copy.yaml` reports nothing.
- [ ] **Step 5: Commit.** `git add deploy/aws/glacier-copy.yaml tests/test_glacier_copy_template.py`

### Task 2: Ledger table and image dependency

**Files:**
- Create: `schema/013_package_copy.sql`
- Modify: `scripts/apply-ledger-migrations.sh` (add the file to the list), `ingest-worker/requirements.txt` (add `awscrt>=0.23`)
- Test: `tests/test_package_copy_schema.py`

**Interfaces:**
- Produces: table `package_copy (id bigserial, game_ingest_id bigint REFERENCES game_ingest ON DELETE CASCADE, location text, bucket text, prefix text, object_count integer, bytes bigint, tagmanifest_sha256 text, copied_at timestamptz, verified_at timestamptz, UNIQUE (game_ingest_id, location))`. `location` is `aws-deep-archive` for now.

- [ ] **Step 1: Write the failing tests.** The migration file creates `package_copy` with the unique constraint, uses `IF NOT EXISTS` so it can run twice, and is listed in `apply-ledger-migrations.sh`. A second test imports `awscrt` and checks that `botocore` can compute a CRC64NVME (`botocore.httpchecksum.CrtCrc64NvmeChecksum` exists and hashes `b"abc"` to the published test value).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Add the migration, the script line and the requirement.**
- [ ] **Step 4: Run them in the rebuilt ingest-worker image** (scratchpad `pt.sh`). Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 3: Copy one package

**Files:**
- Create: `scripts/glacier_copy_core.py`
- Test: `tests/test_glacier_copy_core.py` (use a `FakeS3` like the one in `tests/test_wasabi_verification.py`, extended with multipart calls and `ChecksumCRC64NVME`)

**Interfaces:**
- Consumes: `make_s3_client`, `load_wasabi_settings` from `scripts/upload_bag_wasabi.py`; `parse_manifest` from `scripts/wasabi-fixity-check.py` (move it to `preservation_common.py` if importing the hyphenated file is awkward).
- Produces:
  - `plan_package(source: dict[str, int], target: dict[str, TargetObject], expected: dict[str, str]) -> PackagePlan` with `upload: list[str]`, `delete_marker: list[str]`, `unchanged: list[str]`. A file is unchanged when the target has the same size and its `sha256` metadata equals the expected digest.
  - `copy_file(src_s3, src_bucket, key, dst_s3, dst_bucket, dst_key, expected_sha256: str | None, part_size: int = 64 * 2**20) -> CopyResult` with `sha256`, `crc64nvme`, `bytes`, `ok`, `problem`.
  - `copy_package(...) -> PackageResult` with `files_uploaded`, `files_unchanged`, `delete_markers`, `bytes_read`, `problems`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_small_file_uploads_with_sha256_metadata_and_deep_archive():
    r = copy_file(src, "hitsave-dumps", "dumps/a/manifest-sha256.txt", dst, "copy", "hitsave-dumps/dumps/a/manifest-sha256.txt", sha256_of(DATA))
    obj = dst.objects["hitsave-dumps/dumps/a/manifest-sha256.txt"]
    assert r.ok and obj.storage_class == "DEEP_ARCHIVE" and obj.metadata["sha256"] == sha256_of(DATA)

def test_large_file_sends_full_object_crc64nvme():
    copy_file(..., part_size=5 * 2**20)   # 12 MiB file, three parts
    assert dst.completed[-1]["ChecksumType"] == "FULL_OBJECT"
    assert dst.completed[-1]["ChecksumCRC64NVME"] == crc64nvme_b64(DATA)

def test_wrong_sha256_aborts_the_upload_and_reports_damage():
    r = copy_file(..., expected_sha256="0" * 64)
    assert not r.ok and "differs from the bag manifest" in r.problem
    assert dst.aborted and key not in dst.objects

def test_file_without_expected_digest_is_copied_and_recorded():
    r = copy_file(..., expected_sha256=None)   # tagmanifest-sha256.txt
    assert r.ok and dst.objects[key].metadata["sha256"] == sha256_of(DATA)

def test_plan_skips_unchanged_uploads_changed_and_marks_removed():
    p = plan_package(source={"a": 3, "b": 5}, target={"a": T(3, sha_a), "old": T(9, sha_x)}, expected={"a": sha_a, "b": sha_b})
    assert p.unchanged == ["a"] and p.upload == ["b"] and p.delete_marker == ["old"]
```

- [ ] **Step 2: Run them.** `pytest tests/test_glacier_copy_core.py -v`. Expected: FAIL, module missing.
- [ ] **Step 3: Implement.** Stream the Wasabi body in `part_size` chunks and feed each chunk to `hashlib.sha256` and the CRT CRC64NVME. Files at or under one part go up with one `PutObject`; larger files use `CreateMultipartUpload` with `ChecksumAlgorithm="CRC64NVME"` and `ChecksumType="FULL_OBJECT"`. Compare the SHA-256 before `CompleteMultipartUpload`, or before `PutObject` for single-part files (buffer those in memory: they're at most 64 MiB). After each upload, `HeadObject` with `ChecksumMode="ENABLED"` must return the same size, CRC64NVME and `sha256` metadata.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 4: The copy job

**Files:**
- Create: `scripts/glacier-copy.py`, `config/preservation/aws-copy.yaml.example`
- Test: `tests/test_glacier_copy_job.py`

**Interfaces:**
- Consumes: Task 3's `copy_package`; Task 2's `package_copy`.
- Produces: CLI `glacier-copy.py [--execute] [--max-packages N] [--max-gb G] [--game-key K ...] [--kind KIND]`. The last line of output is one JSON summary: `{"packages_due", "packages_copied", "failed", "skipped_changed", "gb_read", "executed"}`. `aws-copy.yaml` holds `region`, `bucket`, `credentials_file` and `storage_class`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_due_means_no_copy_or_a_different_tagmanifest():
    assert due_game_keys(rows) == ["never-copied", "changed-since-copy"]

def test_report_only_by_default_writes_nothing():
    run(["--max-packages", "5"]); assert fake_dst.puts == [] and ledger.package_copy == []

def test_tagmanifest_changed_during_copy_records_nothing():
    src.change_tagmanifest_after_first_get("pkg")
    out = run(["--execute", "--game-key", "pkg"])
    assert out["skipped_changed"] == 1 and ledger.package_copy == [] and ledger.fixity_run == []

def test_success_records_package_copy_and_a_glacier_fixity_row():
    run(["--execute", "--game-key", "pkg"])
    assert ledger.package_copy[0]["tagmanifest_sha256"] == sha256_of(TAGMANIFEST)
    assert ledger.fixity_run[0]["detail"].startswith("glacier-copy:")

def test_access_denied_stops_the_run_without_recording_failures():
    dst.deny_all()
    with pytest.raises(SystemExit) as e: run(["--execute"])
    assert e.value.code != 0 and ledger.fixity_run == []
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Select packages the same way `wasabi-fixity-check.py` does (`status = 'complete'` and a storage prefix), plus each package's `disc_contents_prefix` as a second prefix. Order: never copied first, then oldest copy. Read the tag manifest before and after; on a change, skip. On success, upsert `package_copy` and insert a `fixity_run` row with outcome `pass`; on a SHA-256 mismatch, insert `fail` with the file names. Treat `AccessDenied`, `InvalidAccessKeyId` and `ExpiredToken` as fatal for the whole run.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 5: Monthly check without restores

**Files:**
- Create: `scripts/glacier-copy-check.py`
- Test: `tests/test_glacier_copy_check.py`

**Interfaces:**
- Produces: CLI `glacier-copy-check.py [--no-record] [--game-key K ...]`. Writes `fixity_run` rows with detail starting `glacier:` and sets `package_copy.verified_at`.

- [ ] **Step 1: Write the failing tests.** A package passes when every manifest file exists in S3 with the same size and `sha256` metadata. It fails, naming the files, when a file is missing, has a different size, or has a different `sha256`. An extra file in S3 that's still current on Wasabi is fine; one that's gone from Wasabi but still current in S3 is reported as "not marked deleted".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** with `ListObjectVersions` for the prefix plus one `HeadObject` per file. Run every package each month (about $0.05).
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 6: Restore script

**Files:**
- Create: `scripts/glacier-restore.py`
- Test: `tests/test_glacier_restore.py`

**Interfaces:**
- Produces: CLI with three subcommands. `request --game-key K [--file REL] [--tier Bulk|Standard]` starts a restore for 7 days. `status --game-key K` lists which files are ready. `fetch --game-key K --to DIR` downloads the ready files and checks each SHA-256 against the bag manifest. Nothing in it writes to Wasabi.

- [ ] **Step 1: Write the failing tests.** `request` calls `RestoreObject` once per file, with the chosen tier. `fetch` refuses to overwrite files in `DIR`, and reports a file whose SHA-256 differs from the manifest.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 7: Windmill jobs and ledger display

**Files:**
- Create: `windmill_ops/wmill/f/hitsave/glacier_copy.{sh,script.yaml,schedule.yaml}`, `windmill_ops/wmill/f/hitsave/glacier_copy_check.{sh,script.yaml,schedule.yaml}`
- Modify: `ledger/item_page.py`, `ledger/ledger_health.py`, `windmill_ops/README.md`
- Test: `tests/test_ledger_item_page.py` (or the existing item-page test), `tests/test_ledger_health.py`

**Interfaces:**
- Consumes: `package_copy`, and `fixity_run` rows starting with `glacier`.

- [ ] **Step 1: Write the failing tests.** The item page shows "Second copy: verified 2026-11-20 (AWS Glacier Deep Archive)", or "Second copy: none yet", or "Second copy: out of date" when a newer bag exists. Health shows the count of complete packages without an up-to-date copy, linked to a filtered ledger list.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** The job wrappers follow `wasabi_fixity_check.sh`, and mount the scripts from the checkout. Schedules: `glacier_copy` daily at 06:15 UTC with `--execute --max-gb 200`, after the catalog-snapshot job at 05:30. `glacier_copy_check` at 07:00 UTC on the 1st of each month. Both schedules start **disabled**.
- [ ] **Step 4: Run them, then validate on the target:** `scripts/windmill-sync-push.sh --dry-run` lists exactly the six new files.
- [ ] **Step 5: Commit.**

### Task 8: Docs

**Files:**
- Create: `docs/glacier-copy.md` (what's copied, how it's checked, how to restore one file or a package, what it costs)
- Modify: `docs/storage-retention.md` (add the second copy and its checks), `AGENTS.md` (one line under storage)

- [ ] **Step 1: Write the docs** in the house style. Include the restore steps with real commands, and the time each tier takes (Bulk up to 48 hours, Standard up to 12).
- [ ] **Step 2: Commit and open the PR** for Tasks 1 to 8. Merge on green.

---

## Rollout

Each step needs Jonas's OK before it runs.

1. **Deploy the stack** in us-east-2 with the `hitsave` profile. Create the access key for the writer user in the console, and save it to `hitsave-archiver-config/secrets/aws-glacier-copy.yaml`.
2. **Pilot:** `glacier-copy.py --execute` for five packages: a DVD over 5 GB, a Redump CD, a No-Intro ROM, a press kit with many small files, and a disc-contents bag. Then run `glacier-copy-check.py` on them.
3. **Restore test:** request a Bulk restore of two pilot packages, fetch them after it completes, and check every file. This also proves the restore steps in `docs/glacier-copy.md`.
4. **Backfill** in batches of about 300 GB, one batch per night, until every package is copied.
5. **Turn on both schedules.** From then on, new and changed packages are copied each night.
6. **Update the Notion page "HitSave Preservation Levels":** Storage reaches level 1 and Integrity reaches level 4. Delete the finished to-do, as we do for resolved items.

## What this plan doesn't do

- Copy `hitsave-incoming`. Its press kits have originals on the on-site share.
- Repair damaged files on Wasabi automatically. The restore script fetches and checks a file; putting it back on Wasabi stays a manual step with an OK.
- Copy the ledger and Omeka database backups. They already go to Google Drive every night. Adding them here later is cheap.
- Delete the duplicates in `hitsave-incoming/processed/`. The storage plan makes that depend on this copy; it gets its own plan.
