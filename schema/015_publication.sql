-- When each Omeka item was first published, and whether the Discord digest has announced it
-- (docs/discord-digest.md).
--
-- publication: one row per Omeka item, written the first time it is published.
--   omeka_item_id       the published item; publishing it again later adds nothing
--   game_ingest_id      the ledger package for the item, when there is one
--   published_at        when the ledger saw the item become public
--   published_by        who published it in the ledger, or 'reconcile' / 'backfill'
--   source              ledger:    published from the ledger UI
--                       reconcile: found public by the digest job (published from a script or
--                                  the Omeka admin); published_at is when it was found
--                       backfill:  already public when the digest started; never announced
--   announced_at        when the digest posted it, or decided not to (see skip_reason)
--   discord_message_id  the Discord message that announced it
--   skip_reason         set when the item was closed without a post: deleted, backfill
--   last_checked_at     last run that skipped the item (hidden again, placeholder description);
--                       skipped items move behind new ones so they can't block the queue
--
-- digest_state: small key/value settings of the digest job; 'backfilled' holds the UTC time of
-- the backfill, and the job refuses to post before it is set.
CREATE TABLE IF NOT EXISTS publication (
    id BIGSERIAL PRIMARY KEY,
    omeka_item_id INTEGER NOT NULL UNIQUE,
    game_ingest_id BIGINT REFERENCES game_ingest (id) ON DELETE SET NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('ledger', 'reconcile', 'backfill')),
    announced_at TIMESTAMPTZ,
    discord_message_id TEXT,
    skip_reason TEXT,
    last_checked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS publication_pending ON publication (published_at) WHERE announced_at IS NULL;

CREATE TABLE IF NOT EXISTS digest_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
