# Restore from the Glacier copy

Every complete package on Wasabi has a second copy in S3 Glacier Deep Archive, in the AWS bucket
`hitsave-backup-glacier` ([glacier-copy.md](glacier-copy.md)). This page explains how to get
files back from that copy, from one damaged file up to the loss of all of Wasabi.

Archivists don't run these steps. They report the problem with **Report a problem** on the
package's ledger page; see
[If a preserved file is damaged or lost](archivist-guide.md#if-a-preserved-file-is-damaged-or-lost)
in the archivist guide. The steps below are for admins.

To repair a package, use the ledger first ([Restore from the ledger](#restore-from-the-ledger)).
The command-line steps further down are for when the ledger is down, when a request ends as
`cannot_restore`, and for the loss of all of Wasabi.

## Restore from the ledger

Admins are the emails listed under `restore.admins` in the private `ledger.yaml`. Only they see
the restore form and the **Approve** and **Cancel** buttons. Anyone signed in can report a
problem.

1. Open the package's ledger page. Under **Problems and restores**, choose the speed (Bulk or
   Standard), say why, and click **Restore from Glacier**. The form shows the cost for the whole
   package as an upper limit. If the last Wasabi check passed, the form warns that the files may
   not need a restore.
2. Within 15 minutes, the Windmill job `glacier_restore_worker` checks every file of the package
   on Wasabi and gives each one a label:

   | Label | Meaning | Restored? |
   |-------|---------|-----------|
   | fine | Matches the bag's manifest, and the copy has the same version | No |
   | newer on Wasabi | Matches the bag's manifest, but the copy has an older version. Wasabi changed after the last copy, which is normal | No |
   | missing | The bag lists it, but it isn't on Wasabi | Yes, when the copy has the version the manifest lists |
   | corrupted | Doesn't match the bag's manifest | Yes, when the copy has the version the manifest lists |

   The job trusts the bag's manifests on Wasabi when `manifest-sha256.txt` matches its line in
   `tagmanifest-sha256.txt`. Otherwise it checks every file against the digests stored with the
   copy. If a manifest is damaged and the bag also changed after its last copy, a file that
   differs may be newer than the copy, so the job restores nothing and explains why.

   - When no file is missing or corrupted, the request ends as **Not needed**. Nothing is
     requested from AWS, so it costs nothing.
   - When the copy only has older versions of the bad files, it ends as **Can't restore from the
     copy**. Use the command-line steps and [Damaged files](damaged-files.md) to decide what to do.
   - Otherwise the job asks AWS to restore only the files it can fix.
3. When AWS has restored the files (up to 48 hours for Bulk, 12 for Standard), the job reads each
   one and checks its SHA-256. A mismatch ends the request as **Failed**.
4. The request then waits for approval. The page lists each file, its label and what will be
   written. Click **Approve** to let the job write the files to Wasabi, or **Cancel**. Approve
   within 7 days, because the restored files stop being readable after that, and the request ends
   as **Expired**.
5. After approval, the job takes the package's write lock, so no other job changes the bag until
   step 6 ends (see [the packaging standard](preservation-packaging-standard.md)). It then writes each file to Wasabi. A file completes only when its bytes match
   the copy's SHA-256, so a bad read never replaces a Wasabi file. If someone fixed a file in the
   meantime, the job skips it.
6. The job runs the Wasabi fixity check for the package. When it passes, the job adds a PREMIS
   `recovery` event to the bag, closes the linked report, and the request ends as **Done**. The
   new event changes the bag, so the next nightly `glacier_copy` run copies the package again.

The **Restores** page in the ledger lists open reports and requests. Each action is logged in the
ledger container's log as `action=restore-...`.

Setting it up: apply `schema/014_restore.sql` to the ledger database before deploying the ledger
version that shows the panel, add the admins to `restore.admins`, and turn on the
`glacier_restore_worker` schedule.

## What the copy holds

- Each Wasabi file is stored at `<wasabi bucket>/<key>`, for example
  `hitsave-dumps/dumps/Redump/DVD-Video/Coverdiscs/F/<package>/data/<file>.iso`.
- Each file has the SHA-256 from the bag manifest as `x-amz-meta-sha256`. The copy job uploads a
  file only when its bytes match the manifest, so a file in the copy is the file the bag describes.
- A file that was damaged when the archive received it is stored as received. Restoring it gives
  back the same damaged file; see [damaged-files.md](damaged-files.md) for replacing it.
- A file that went bad on Wasabi before its first copy isn't in the copy. The copy job reports it
  as a problem on that package's `glacier-copy:` fixity row.
- When a file changes or is removed on Wasabi, the copy keeps the earlier version for 365 days.
- The ledger and the Omeka database aren't in the copy. They have their own backups. Each package
  carries dated copies of its catalog record in `metadata/catalog/`, so catalog records can be
  rebuilt from the packages.

To see whether a package has a copy, open its page in the ledger. The line **Second copy** shows
when it was copied and last checked, or `none yet`.

## How long it takes and what it costs

Deep Archive keeps files offline. AWS must first restore a file, which makes it readable for 7
days, and then the archive can download it.

| Restore speed | Ready within | Restore cost per GB | Use it for |
|---------------|--------------|---------------------|------------|
| Bulk (default) | 48 hours | $0.0025 | Anything that can wait two days |
| Standard | 12 hours | $0.02 | A file that's needed sooner, such as a public item that's broken |

Downloading from AWS is free for the first 100 GB each month and costs $0.09 per GB after that.
One DVD of about 4 GB costs about $0.01 with Bulk. Everything (about 1.2 TB) costs about $107.
The TechSoup credits pay for it.

## Before you start

Use any machine with Docker, a `hitsave-archiver` checkout and the private
`hitsave-archiver-config` checkout beside it. Run every command in the `hitsave-archiver` checkout:

```bash
cd hitsave-archiver
source ../hitsave-archiver-config/host.env
R="docker compose run --rm -T --no-deps -v $PWD/scripts:/app/scripts:ro --entrypoint python ingest-worker"
```

`/output` in the container is the `$HOST_OUTPUT` folder from `host.env`. Restore into a new folder under
`/output/restore/`. The restore script never overwrites a file and never writes to Wasabi.

The script needs the package's game key, which is on its ledger page. If the ledger is down, use
`--prefix` with the copy's key prefix instead of `--game-key`, for example
`--prefix hitsave-dumps/dumps/Redump/DVD-Video/Coverdiscs/F/<package>`. Listing the bucket works
without a restore. With the project owner's AWS login:

```bash
aws s3 ls s3://hitsave-backup-glacier/hitsave-dumps/dumps/ --profile hitsave
```

## Repair a damaged or missing file on Wasabi from the command line

Use this when the ledger is down, or a request ended as `cannot_restore` and you've decided which
version to put back.

Restore the whole package, even when only one file is bad. A package is a few GB at most, the
cost is a few cents, and the upload step below checks the bag as a whole.

1. Start the restore:

   ```bash
   $R /app/scripts/glacier-restore.py request --game-key KEY --tier Bulk
   ```

2. Check whether the files are ready. Repeat this later, up to 48 hours (Bulk) or 12 hours
   (Standard) after step 1:

   ```bash
   $R /app/scripts/glacier-restore.py status --game-key KEY
   ```

   The output lists `ready` and `waiting` files. Go on once `waiting` is empty.

3. Download the package. The script checks each file's SHA-256 against the bag manifest. An empty
   `problems` list means every file matches, and the script exits 0:

   ```bash
   $R /app/scripts/glacier-restore.py fetch --game-key KEY --to /output/restore/KEY
   ```

4. Check the bag as a whole. Both commands must finish without output:

   ```bash
   cd "$HOST_OUTPUT/restore/KEY"
   sha256sum -c --quiet manifest-sha256.txt
   sha256sum -c --quiet tagmanifest-sha256.txt
   cd -
   ```

5. Put the package back on Wasabi, at the prefix the ledger has for it. The script uploads every
   file and checks each upload. It stops if Wasabi holds files under that prefix that aren't in
   the bag, and lists them so you can decide what to do:

   ```bash
   $R /app/scripts/upload-bag-wasabi.py /output/restore/KEY KEY
   ```

6. Check the package on Wasabi again, so the ledger records a passing check:

   ```bash
   $R /app/scripts/wasabi-fixity-check.py --game-key KEY
   ```

7. Delete `$HOST_OUTPUT/restore/KEY` once the check passes.

The bag's files are the same as before, so its `tagmanifest-sha256.txt` doesn't change and the
nightly Glacier copy has nothing new to copy.

## Get back a version from before a change

Use this when a file was changed or removed by mistake on Wasabi and the nightly copy has already
copied the change. The earlier version stays in the copy for 365 days after it was replaced.

The restore script works only on current versions, and the job's access key can't read older
ones. The project owner restores older versions in the AWS Management Console:

1. Open the bucket `hitsave-backup-glacier` in the S3 console and go to the file's folder.
2. Turn on **Show versions** and select the version from before the change. Its **Metadata**
   shows `x-amz-meta-sha256`.
3. Choose **Actions**, **Initiate restore**, and pick Bulk or Standard and the number of days.
4. When the restore is done, download the version and check its SHA-256 against the metadata
   value with `sha256sum`.
5. Put the file back into the bag and upload the bag as in steps 4–6 above.

If the nightly copy hasn't run since the mistake, the current version in the copy is still the
right one. Use the steps for a damaged file instead.

## Lose all of Wasabi

Use this if the Wasabi buckets are lost, for example because the account is closed or the
provider fails. The copy is the only other copy of the packages, so work slowly and check every
step.

1. Decide where the new permanent copy goes: new Wasabi buckets or another S3 provider. Point the
   `wasabi:` section of `config/preservation/ingest.yaml` (endpoint and buckets) at it, and the
   credentials in `hitsave-archiver-config/secrets/wasabi-credentials.yaml`.
2. Make sure the machine you restore to has room. Everything is about 1.2 TB.
3. Start the restore of each bucket. This sends one request per file, about 134,000 in all:

   ```bash
   $R /app/scripts/glacier-restore.py request --prefix hitsave-dumps --tier Bulk
   $R /app/scripts/glacier-restore.py request --prefix hitsave-press-material --tier Bulk
   ```

4. After 48 hours, check that nothing is still waiting. The output lists every file, so count
   them:

   ```bash
   $R /app/scripts/glacier-restore.py status --prefix hitsave-dumps | jq '.waiting | length'
   ```

5. Download each bucket within 7 days of the restore finishing. Restored files stop being
   readable after that, and a new restore costs the same again:

   ```bash
   $R /app/scripts/glacier-restore.py fetch --prefix hitsave-dumps --to /output/restore/hitsave-dumps
   $R /app/scripts/glacier-restore.py fetch --prefix hitsave-press-material --to /output/restore/hitsave-press-material
   ```

6. Upload each package with `upload-bag-wasabi.py`, as in step 5 of the repair. This needs the
   ledger for each package's game key and prefix. There's no script yet that loops over every
   package, so write one for the occasion and test it on a few packages first.
7. Run `wasabi-fixity-check.py` on every package, and check that each one passes.
8. If the press bucket's name changed, update Omeka's `dip-s3.config.php`
   ([dip-wasabi-s3.md](dip-wasabi-s3.md)) so the catalog can read the packages again.

Restore the ledger and Omeka from their own backups. If Omeka is lost too, each package's
`metadata/catalog/` holds its catalog record.

## Check that restores work

The first restore test started on 2026-11-14 with one DVD package and one press kit, using Bulk.
Repeat a restore test once a year, as for the other backups.
