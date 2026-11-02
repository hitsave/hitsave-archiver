"""One-shot Redump relocation: prefix mapping and Omeka string replacement."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("reloc", ROOT / "scripts" / "relocate_redump_bags_to_redump_prefix.py")
reloc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reloc)

OLD = "dumps/DVD-Video/Coverdiscs/F/famitsu-wave-dvd-2008-12-01a136e7"
NEW = "dumps/Redump/DVD-Video/Coverdiscs/F/famitsu-wave-dvd-2008-12-01a136e7"


def test_target_prefix_and_idempotence() -> None:
    assert reloc.redump_target_prefix(OLD) == NEW
    assert reloc.redump_target_prefix(OLD + "/") == NEW
    assert reloc.redump_target_prefix(NEW) == NEW


@pytest.mark.parametrize("bad", ["dumps/No-Intro/Nintendo - Nintendo 64/F/x", "dumps/Others/X/y",
                                 "dumps/Nintendo Switch/B/z", "press-material/F/x"])
def test_refuses_non_redump_layouts(bad: str) -> None:
    with pytest.raises(ValueError):
        reloc.redump_target_prefix(bad)


def test_replace_in_media_data_and_uri() -> None:
    data = {
        "package_storage_prefix": OLD,
        "dip_wasabi_uri": f"s3://hitsave-dumps/{OLD}/",
        "package_wasabi_uri": f"s3://hitsave-dumps/{OLD}/",
        "dip_size_bytes": 123,
    }
    out = reloc.replace_in(data, OLD, NEW)
    assert out["package_storage_prefix"] == NEW and out["dip_wasabi_uri"] == f"s3://hitsave-dumps/{NEW}/"
    assert out["dip_size_bytes"] == 123
    assert not reloc.contains(out, OLD + "/") and reloc.contains(data, OLD)
    # running again changes nothing (old is not a substring of new)
    assert reloc.replace_in(out, OLD, NEW) == out
    uris = [{"type": "uri", "@id": f"s3://hitsave-dumps/{OLD}/", "is_public": False},
            {"type": "uri", "@id": "https://redump.info/disc/91919/"}]
    swapped = reloc.replace_in(uris, f"s3://hitsave-dumps/{OLD}/", f"s3://hitsave-dumps/{NEW}/")
    assert swapped[0]["@id"].endswith(f"{NEW}/") and swapped[0]["is_public"] is False
    assert swapped[1] == uris[1]
