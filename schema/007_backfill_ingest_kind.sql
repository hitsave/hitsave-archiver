-- Omeka/catalog routing for rows ingested before ingest_kind was required on the ledger.
UPDATE game_ingest
SET ingest_kind = 'press_material'
WHERE ingest_kind IS NULL
  AND (
    source_path LIKE '/data/press-material/%'
    OR source_path LIKE '%Representation_Copies/%'
  );
