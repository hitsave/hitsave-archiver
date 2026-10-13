-- Unify legacy game_dump / disc_dump ledger values under ingest kind dump.
UPDATE game_ingest
SET ingest_kind = 'dump'
WHERE ingest_kind IN ('game_dump', 'disc_dump');
