-- Problem reports and restores from the Glacier copy (docs/restore-from-glacier.md).
--
-- problem_report: anyone signed in to the ledger reports a problem with a package.
--   note                what the person saw
--   reported_by/at      who reported it and when
--   closed_by/at/note   an admin closes it, or the restore job closes it after a successful restore
--
-- restore_request: an admin asks for a restore; scripts/glacier-restore-worker.py moves it forward.
--   state               checking -> restoring -> awaiting_approval -> approved -> writing -> done.
--                       Finished states: not_needed (no file is missing or corrupted),
--                       cannot_restore (the copy only has older versions of the bad files),
--                       done, failed, cancelled, expired (restored files stopped being readable
--                       before they were written). scripts/restore_states.py lists them too.
--   tier                Bulk (48 hours) or Standard (12 hours)
--   reason              why the admin asked
--   approved_by/at      the admin who allowed the write to Wasabi
--   files               one entry per file: bag, rel, size, sha256 (the copy's), verdict
--                       (ok, newer, missing, corrupted), restorable, written (yes, skipped, no), note
--   message             what the job last said about the request
--   restore_expires_at  when the restored files stop being readable in AWS
--   finished_at         when the request reached a finished state
CREATE TABLE IF NOT EXISTS problem_report (
    id BIGSERIAL PRIMARY KEY,
    game_ingest_id BIGINT NOT NULL REFERENCES game_ingest (id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    reported_by TEXT NOT NULL,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_by TEXT,
    closed_at TIMESTAMPTZ,
    close_note TEXT
);

CREATE TABLE IF NOT EXISTS restore_request (
    id BIGSERIAL PRIMARY KEY,
    game_ingest_id BIGINT NOT NULL REFERENCES game_ingest (id) ON DELETE CASCADE,
    problem_report_id BIGINT REFERENCES problem_report (id),
    state TEXT NOT NULL DEFAULT 'checking' CHECK (state IN (
        'checking', 'restoring', 'awaiting_approval', 'approved', 'writing',
        'not_needed', 'cannot_restore', 'done', 'failed', 'cancelled', 'expired')),
    tier TEXT NOT NULL CHECK (tier IN ('Bulk', 'Standard')),
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    files JSONB NOT NULL DEFAULT '[]',
    message TEXT,
    restore_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS restore_request_one_open ON restore_request (game_ingest_id)
    WHERE state IN ('checking', 'restoring', 'awaiting_approval', 'approved', 'writing');
CREATE INDEX IF NOT EXISTS problem_report_open ON problem_report (game_ingest_id) WHERE closed_at IS NULL;
