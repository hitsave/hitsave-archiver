#!/usr/bin/env python3
"""Move each open Glacier restore request one step forward (docs/restore-from-glacier.md).

Admins start restores in the ledger. Windmill runs this script every 15 minutes:

  checking           label every file on Wasabi (ok, newer, missing, corrupted); ask AWS to restore
                     only the missing and corrupted files the copy can fix. Ends as not_needed when
                     nothing is wrong, or cannot_restore when the copy only has older versions.
  restoring          wait until AWS has restored every file, then read each one and check its SHA-256
  awaiting_approval  nothing is written until an admin approves in the ledger; expires after 7 days
  approved, writing  write each file back to Wasabi (skipping files that are correct by now), run
                     the Wasabi fixity check, add a PREMIS "recovery" event, close the linked report

Every state change only applies if the request is still in the state the job read, so an admin's
cancel or approve in the ledger is never overwritten. Access errors stop the run with exit code 2.

Usage (ingest-worker; scripts mounted so the image need not contain this file):
    docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \\
      --entrypoint python ingest-worker /app/scripts/glacier-restore-worker.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from botocore.exceptions import ClientError  # noqa: E402

from glacier_copy_core import LOCATION, TAGMANIFEST, FatalError, connect, is_fatal, prefixes_of  # noqa: E402
from glacier_restore_core import (  # noqa: E402
    FileCheck,
    check_bag,
    request,
    restore_progress,
    summary_message,
    verify_restored,
    write_back,
)
from premis_events import PremisEvent, stage_event  # noqa: E402
from restore_states import FINISHED, OPEN  # noqa: E402

PROGRAM = "glacier-restore-worker.py"
WORKER = "glacier-restore-worker"
EXPIRED_MESSAGE = "The restored files stopped being readable before anyone approved them. Start a new restore."
FIXITY_FAILED_MESSAGE = "The Wasabi fixity check failed after the restore. See the fixity row on the ledger page."
REPORT_CLOSED_NOTE = "Restored from the Glacier copy"


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    return ap.parse_args(argv)


def _files(req: dict) -> list[FileCheck]:
    raw = req.get("files") or []
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [FileCheck.from_json(d) for d in raw]


def _to_restore(files: list[FileCheck]) -> list[FileCheck]:
    return [f for f in files if f.restorable]


def _expired(req: dict, now: datetime) -> bool:
    expires = req.get("restore_expires_at")
    return expires is not None and now >= expires


def done_message(written: int, restorable: int) -> str:
    text = f"Restored {written} {'file' if written == 1 else 'files'}."
    if written < restorable:
        text += f" {restorable - written} were already correct on Wasabi, so they weren't written."
    return text


class Step:
    """One request, one step. Each save names the state it expects, so concurrent ledger changes win."""

    def __init__(self, req: dict, *, ledger, aws_s3, aws_bucket, wasabi_s3, bucket_for, fixity_check, now):
        self.req, self.ledger = req, ledger
        self.aws_s3, self.aws_bucket, self.wasabi_s3 = aws_s3, aws_bucket, wasabi_s3
        self.wasabi_bucket = bucket_for(req["ingest_kind"], req["package_storage_prefix"])
        self.fixity_check, self.now = fixity_check, now

    def save(self, expect: str, **fields) -> bool:
        if "files" in fields:
            fields["files"] = [f.to_json() for f in fields["files"]]
        ok = self.ledger.save(self.req["id"], expect, **fields)
        if ok:
            self.req.update(fields)
        return ok

    def run(self) -> str:
        state = self.req["state"]
        getattr(self, state)()
        return self.req["state"]

    def checking(self) -> None:
        recorded = str(self.req.get("tagmanifest_sha256") or "").split(",")
        files: list[FileCheck] = []
        for i, prefix in enumerate(prefixes_of(self.req)):
            files += check_bag(self.wasabi_s3, self.wasabi_bucket, prefix, self.aws_s3, self.aws_bucket,
                               recorded[i] if i < len(recorded) else "")
        state, message = summary_message(files)
        if state == "restoring":
            by_bag: dict[str, list[str]] = {}
            for f in _to_restore(files):
                by_bag.setdefault(f.bag, []).append(f.rel)
            for bag, rels in by_bag.items():
                request(self.aws_s3, self.aws_bucket, bag, rels, self.req["tier"])
        self.save("checking", state=state, message=message, files=files)

    def restoring(self) -> None:
        files = _to_restore(_files(self.req))
        ready, waiting, expiry = restore_progress(self.aws_s3, self.aws_bucket, files)
        if waiting:
            return
        problems = verify_restored(self.aws_s3, self.aws_bucket, files)
        if problems:
            self.save("restoring", state="failed", message="\n".join(problems))
            return
        self.save("restoring", state="awaiting_approval", restore_expires_at=expiry)

    def awaiting_approval(self) -> None:
        if _expired(self.req, self.now()):
            self.save("awaiting_approval", state="expired", message=EXPIRED_MESSAGE)

    def approved(self) -> None:
        if _expired(self.req, self.now()):
            self.save("approved", state="expired", message=EXPIRED_MESSAGE)
            return
        if self.save("approved", state="writing"):
            self.writing()

    def writing(self) -> None:
        if _expired(self.req, self.now()) and any(
                f.written not in ("yes", "skipped") for f in _to_restore(_files(self.req))):
            self.save("writing", state="expired", message=EXPIRED_MESSAGE)
            return
        files = _files(self.req)
        for f in files:
            if not f.restorable or f.written in ("yes", "skipped"):
                continue
            f.written, note = write_back(self.aws_s3, self.aws_bucket, self.wasabi_s3, self.wasabi_bucket, f)
            if note:
                f.note = note
            if not self.save("writing", files=files):
                return
            if f.written == "no":
                self.save("writing", state="failed", message=f"{f.rel}: {note}")
                return
        if not self.fixity_check(self.req["game_key"]):
            self.save("writing", state="failed", message=FIXITY_FAILED_MESSAGE)
            return
        written = [f for f in files if f.written == "yes"]
        for bag in sorted({f.bag for f in written}):
            self.add_recovery_event(bag, sum(f.bag == bag for f in written))
        if self.save("writing", state="done", message=done_message(len(written), len(_to_restore(files)))):
            if self.req.get("problem_report_id"):
                self.ledger.close_report(self.req["problem_report_id"], WORKER, REPORT_CLOSED_NOTE)

    def add_recovery_event(self, bag: str, count: int) -> None:
        """Add a PREMIS recovery event to the bag on Wasabi: the event file first, then the tag manifest."""
        root = bag.split("/", 1)[1]
        s3, bucket = self.wasabi_s3, self.wasabi_bucket
        try:
            submission = json.loads(s3.get_object(Bucket=bucket, Key=root + "metadata/submission.json")["Body"].read())
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise
            submission = {}
        tm = s3.get_object(Bucket=bucket, Key=root + TAGMANIFEST)["Body"].read().decode("utf-8")
        event = PremisEvent(
            event_type="recovery",
            detail=f"Restored {count} {'file' if count == 1 else 'files'} from the AWS Glacier Deep Archive "
                   f"copy (restore request {self.req['id']})",
            program=PROGRAM,
            package_id=str(submission.get("package_id") or self.req["game_key"]),
            package_uuid=str(submission.get("package_uuid") or ""),
        )
        rel, body, new_tm = stage_event(tm, event, "recovery")
        for key, data, ctype in ((root + rel, body, "application/xml"),
                                 (root + TAGMANIFEST, new_tm.encode("utf-8"), "text/plain")):
            s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=ctype,
                          Metadata={"sha256": hashlib.sha256(data).hexdigest()})
            if s3.get_object(Bucket=bucket, Key=key)["Body"].read() != data:
                raise RuntimeError(f"s3://{bucket}/{key} differs from what was written")


def run(argv: list[str], *, ledger, aws_s3, aws_bucket: str, wasabi_s3, bucket_for, fixity_check, now) -> dict:
    parse_args(argv)
    counts: dict[str, int] = {}
    for req in ledger.open_requests():
        try:
            state = Step(req, ledger=ledger, aws_s3=aws_s3, aws_bucket=aws_bucket, wasabi_s3=wasabi_s3,
                         bucket_for=bucket_for, fixity_check=fixity_check, now=now).run()
        except ClientError as e:
            if is_fatal(e):
                raise FatalError(f"stopping: {e}") from e
            ledger.save(req["id"], req["state"], message=f"Error, will try again: {e}")
            state = "error"
        except Exception as e:  # noqa: BLE001 - one request's problem must not stop the others
            ledger.save(req["id"], req["state"], message=f"Error, will try again: {e}")
            state = "error"
        counts[state] = counts.get(state, 0) + 1
        print(f"{state.upper():18} {req['game_key']} (request {req['id']})", flush=True)
    return counts


class Ledger:
    def __init__(self, conn):
        self.conn = conn

    def open_requests(self) -> list[dict]:
        cur = self.conn.execute(
            """
            SELECT r.id, r.state, r.tier, r.files, r.message, r.problem_report_id, r.restore_expires_at,
                   g.game_key, g.ingest_kind, g.package_storage_prefix, g.disc_contents_prefix,
                   pc.tagmanifest_sha256
            FROM restore_request r
            JOIN game_ingest g ON g.id = r.game_ingest_id
            LEFT JOIN package_copy pc ON pc.game_ingest_id = g.id AND pc.location = %s
            WHERE r.state = ANY(%s)
            ORDER BY r.id
            """,
            (LOCATION, list(OPEN)),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def save(self, request_id: int, expect_state: str, **fields) -> bool:
        from psycopg.types.json import Jsonb

        sets, params = ["updated_at = now()"], []
        for name, value in fields.items():
            sets.append(f"{name} = %s")
            params.append(Jsonb(value) if name == "files" else value)
        if fields.get("state") in FINISHED:
            sets.append("finished_at = now()")
        cur = self.conn.execute(
            f"UPDATE restore_request SET {', '.join(sets)} WHERE id = %s AND state = %s",
            [*params, request_id, expect_state],
        )
        self.conn.commit()
        return cur.rowcount == 1

    def close_report(self, report_id: int, by: str, note: str) -> None:
        self.conn.execute(
            "UPDATE problem_report SET closed_by = %s, closed_at = now(), close_note = %s "
            "WHERE id = %s AND closed_at IS NULL",
            (by, note, report_id),
        )
        self.conn.commit()


def fixity_check(game_key: str) -> bool:
    """Run the Wasabi fixity check for one package; it records its own fixity_run row."""
    script = Path(__file__).resolve().parent / "wasabi-fixity-check.py"
    proc = subprocess.run([sys.executable, str(script), "--game-key", game_key], check=False)
    return proc.returncode == 0


def main() -> int:
    conn, wasabi_s3, aws_s3, aws, bucket_for = connect()
    try:
        summary = run(sys.argv[1:], ledger=Ledger(conn), aws_s3=aws_s3, aws_bucket=str(aws["bucket"]),
                      wasabi_s3=wasabi_s3, bucket_for=bucket_for, fixity_check=fixity_check,
                      now=lambda: datetime.now(timezone.utc))
    except FatalError as e:
        print(f"FATAL {e}", file=sys.stderr, flush=True)
        return 2
    finally:
        conn.close()
    print(json.dumps(summary))
    return 1 if summary.get("error") or summary.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
