-- Packages intake rejected before they reached the ledger (mirror of intake's failed/ folder).
-- Filled by scripts/remote-intake/sync_intake_rejections.py at the end of each intake sweep;
-- the ledger app's Health page reads it when failed/ isn't mounted where the ledger runs.
CREATE TABLE IF NOT EXISTS intake_rejection (
    slug        text PRIMARY KEY,
    lane        text,
    reason      text NOT NULL,
    failed_at   timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
