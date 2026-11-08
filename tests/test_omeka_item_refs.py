"""Every place that takes an Omeka item reference accepts ids, /item/<id> URLs and our ARKs."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from catalog_correct import parse_omeka_item_id as ledger_parse  # noqa: E402


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_omeka_item_id


PARSERS = [ledger_parse] + [_script(n) for n in ("correct-catalog-item", "set-omeka-visibility", "curation-quarantine-omeka")]
GOOD = {
    "345": 345,
    " 16396 ": 16396,
    "https://archive.hitsave.org/ark:/78322/345": 345,
    "https://archive.hitsave.org/ark:/78322/345?x=1": 345,
    "ark:/78322/16396": 16396,
    "ark:78322/16396": 16396,
    "https://archive.hitsave.org/admin/item/42": 42,
    "https://archive.hitsave.org/s/main/item/42?foo": 42,
}
BAD = ["https://archive.hitsave.org/ark:/99999/345", "https://example.org/ark:/78322/x", "nonsense", "/item/"]


@pytest.mark.parametrize("parse", PARSERS)
def test_item_refs(parse) -> None:
    for ref, want in GOOD.items():
        assert parse(ref) == want, ref
    for ref in BAD:
        try:
            got = parse(ref)
        except SystemExit:
            got = None
        assert got is None, ref
