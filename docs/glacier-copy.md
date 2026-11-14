# Second copy in S3 Glacier Deep Archive

The archive keeps a second copy of every finished package in Amazon S3 Glacier Deep Archive, in
the HitSave AWS project (us-east-2, Ohio). Wasabi stays the copy that people and tools read
every day. The Glacier copy is the one you restore from if Wasabi loses or damages files.

This page explains what's copied, how the archive checks the copy, how to get files back, and
what it costs.

## What's copied

A nightly job copies every package whose ledger status is `complete`:

- the package itself, from `hitsave-dumps`, `hitsave-press-material`, `hitsave-other` or
  `hitsave-analog`;
- a dump's disc-contents bag (`hitsave-dumps/disc-contents/...`), if it has one.

Each file on Wasabi becomes one file in the bucket `hitsave-backup-glacier`, under the same
name with the Wasabi bucket in front. For example, the disc image of PSWorld Volume 84,
`dumps/Redump/DVD-Video/Coverdiscs/P/psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304/data/18 - PSWorld Volume 84/VOL_84.iso` in `hitsave-dumps`, is stored as
`hitsave-dumps/dumps/Redump/DVD-Video/Coverdiscs/P/psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304/data/18 - PSWorld Volume 84/VOL_84.iso`.

The job doesn't copy `hitsave-incoming`. Its press kits still have their originals on the
on-site press-material share, and each press kit gets its copy once it's ingested.

## How the copy stays up to date

The Windmill job `glacier_copy` runs every night at 06:15 UTC, after the catalog copies are
added to the bags at 05:30. It runs `scripts/glacier-copy.py --execute` and copies up to 50
packages or 200 GB a night.

A package is due when it has no copy yet, or when its `tagmanifest-sha256.txt` on Wasabi has
changed since its last copy. Every change to a bag rewrites that file: a new catalog copy, a
PREMIS event, extracted DVD videos or a rebuilt bag. For a due package, the job:

1. uploads every file that's new or changed, and skips files the copy already has;
2. adds a delete marker for every file that's gone from Wasabi, so the copy matches Wasabi;
3. records the copy in the ledger table `package_copy`, and adds a `fixity_run` row whose
   detail starts with `glacier-copy:`.

If the bag changes while the job copies it, the job records nothing and copies the package
again the next night.

## How the archive checks the copy

The job checks each file while it streams from Wasabi to AWS, so the archive never needs to
restore files to trust the copy:

- It computes the file's SHA-256 and compares it with the bag's `manifest-sha256.txt` or
  `tagmanifest-sha256.txt`. If they differ, the file on Wasabi is damaged. The job uploads
  nothing for that file and records a failed `fixity_run` row that names it.
- It sends a CRC64NVME checksum of the whole file with the upload. AWS rejects the upload if
  the bytes it received don't match.
- It stores the SHA-256 with the file (`x-amz-meta-sha256`), and then asks AWS for the file's
  size, checksum and SHA-256 to confirm what was stored.

The Windmill job `glacier_copy_check` runs at 07:00 UTC on the 1st of each month. It runs
`scripts/glacier-copy-check.py`, which asks AWS for the size and SHA-256 of every file in
every copy (no restores, about $0.05 a run). A package passes when every file in its manifests
is in the copy with the right size and SHA-256. The check also reports files that Wasabi no
longer has but the copy still shows as current. Results are `fixity_run` rows whose detail
starts with `glacier:`, and a pass sets `package_copy.verified_at`.

On the ledger, each complete package's page shows a line such as "Second copy: verified
2026-12-01 (AWS Glacier Deep Archive)", or "Second copy: none yet". The Health page counts the
complete packages that have no up-to-date copy.

## Why nobody can delete the copy for a year

The bucket keeps every version of every file, and locks each version for 365 days
(S3 Object Lock in governance mode):

- When a file changes on Wasabi, the job uploads the new version. The old version stays.
- When a file is removed from Wasabi, the job adds a delete marker. The old version stays.
- After a version has been replaced for 365 days, a lifecycle rule deletes it.

This protects the copy from a broken script, a stolen access key, and a bad change that nobody
notices for months. The job's access key (the AWS user `hitsave-glacier-copy`) can add, read
and restore files, but it can't delete versions or change the lock. In governance mode, the
project owner can still delete a locked version from the AWS console, for example a file
uploaded by mistake.

## Get files back

Files in Deep Archive must be restored before you can download them. Use
`scripts/glacier-restore.py` on saturn, in the archiver checkout. It never writes to Wasabi.
Putting a repaired file back on Wasabi is a separate, manual step.

1. Start the restore. For one file:

   ```bash
   docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
     --entrypoint python ingest-worker /app/scripts/glacier-restore.py \
     request --game-key psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304 \
     --file "data/18 - PSWorld Volume 84/VOL_84.iso"
   ```

   Leave out `--file` to restore the whole package. Add `--tier Standard` if you need the file
   sooner.

2. Check which files are ready:

   ```bash
   docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
     --entrypoint python ingest-worker /app/scripts/glacier-restore.py \
     status --game-key psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304
   ```

   The output lists `ready` and `waiting` files.

3. Download the ready files into a new folder under `/output` (`HOST_OUTPUT` on saturn), for
   example `/output/restore/psworld-volume-84`:

   ```bash
   docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
     --entrypoint python ingest-worker /app/scripts/glacier-restore.py \
     fetch --game-key psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304 \
     --to /output/restore/psworld-volume-84
   ```

   The script never overwrites a file. It checks each file's SHA-256 against the bag manifest,
   and lists every file that differs under `problems`. An empty `problems` list means every
   file matches.

If the ledger isn't available, use `--prefix "hitsave-dumps/dumps/Redump/DVD-Video/Coverdiscs/P/psworld-volume-84-01a1b692-bc46-777c-a3ef-b370cc5a8304"`
instead of `--game-key`.

| Restore speed | Ready within | Cost per GB |
|---------------|--------------|-------------|
| Bulk (default) | 48 hours | $0.0025 |
| Standard | 12 hours | $0.02 |

A restored file stays downloadable for 7 days. Downloading from AWS to saturn costs $0.09 per
GB after the first 100 GB each month.

## What it costs

AWS list prices for us-east-2, read on 2026-11-13. The TechSoup credits pay for all of this.

| Item | Cost |
|------|------|
| Storage, about 1.2 TB | $1.20 a month ($0.00099 per GB-month) |
| Uploads | $0.05 per 1,000 files; the first full copy of 133,675 files cost about $7 |
| Monthly check | about $0.05 a run |
| Every version is billed for at least | 180 days |
| Restoring everything after losing Wasabi | about $107 (Bulk restore plus download) |

## Set it up

The AWS resources are in `deploy/aws/glacier-copy.yaml`, a CloudFormation template. It creates
the bucket and the AWS user `hitsave-glacier-copy`, but no access key. The project owner deploys
it once in us-east-2, creates an access key for that user in the AWS console, and saves it to
`hitsave-archiver-config/secrets/aws-glacier-copy.yaml` (template:
`config/secrets/aws-glacier-copy.yaml.example`). The region and bucket name are in
`config/preservation/aws-copy.yaml`.

Apply `schema/013_package_copy.sql` to the ledger before the ledger update that shows the
second copy goes live.
