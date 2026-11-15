"""States of a Glacier restore request (schema/014_restore.sql, docs/restore-from-glacier.md)."""
from __future__ import annotations

# A package has at most one request in an open state.
OPEN = ("checking", "restoring", "awaiting_approval", "approved", "writing")
FINISHED = ("not_needed", "cannot_restore", "done", "failed", "cancelled", "expired")
TIERS = ("Bulk", "Standard")
