"""Unit tests for sync_dump_bag_metadata_from_ledger helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_dump_bag_metadata_from_ledger import (  # noqa: E402
    patch_bag_info_external_description,
    patch_submission_json,
    update_manifest_digest,
)


def test_update_manifest_digest():
    manifest = "aaa  bag-info.txt\nbbb  metadata/submission.json\n"
    out = update_manifest_digest(manifest, "bag-info.txt", "ccc")
    assert out == "ccc  bag-info.txt\nbbb  metadata/submission.json\n"
    manifest_single = "aaa bagit.txt\n"
    out2 = update_manifest_digest(manifest_single, "bagit.txt", "ccc")
    assert out2 == "ccc bagit.txt\n"


def test_patch_submission_json():
    sub = {
        "package_id": "old-slug-uuid",
        "wasabi_key": "dumps/old",
        "omeka_item_title": "Title",
    }
    ledger = {"game_key": "new-slug-uuid", "title_slug": "new-slug", "sort_letter": "F"}
    out = patch_submission_json(
        sub,
        ledger=ledger,
        storage_prefix="dumps/DVD-Video/Coverdiscs/F/new-slug-uuid",
        wasabi_uri="s3://hitsave-dumps/dumps/DVD-Video/Coverdiscs/F/new-slug-uuid/",
    )
    assert out["package_id"] == "new-slug-uuid"
    assert out["game_key"] == "new-slug-uuid"
    assert out["wasabi_key"].endswith("new-slug-uuid")
    assert json.loads(json.dumps(out))["sort_letter"] == "F"


def test_patch_bag_info_external_description():
    body = "Bag-Software: test\nExternal-Description: old\nPayload-Oxum: 1\n"
    out = patch_bag_info_external_description(
        body,
        omeka_title="T",
        package_id="pkg-1",
        wasabi_uri="s3://b/p/",
        submission_date="2026-01-01",
    )
    assert "package_id=pkg-1" in out
    assert "wasabi=s3://b/p/" in out
    assert "Bag-Software: test" in out
