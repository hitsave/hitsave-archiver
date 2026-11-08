"""correct-catalog-item.py: "Donated by" (dcterms:provenance), and items without a ledger row."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("correct_item", ROOT / "scripts" / "correct-catalog-item.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


class _Session:
    def patch(self, url, **kw):
        return type("R", (), {"ok": True, "status_code": 200, "text": ""})()


@pytest.fixture
def omeka(monkeypatch):
    calls: dict = {}
    creds = {"key_identity": "i", "key_credential": "c"}
    monkeypatch.setattr(cc, "_omeka_session", lambda: (_Session(), "http://omeka/api", creds))
    monkeypatch.setattr(cc, "_fetch_item", lambda *a: {"o:title": "FF7 AC Trailer"})
    monkeypatch.setattr(cc.register_api, "resolve_property_id", lambda s, b, c, term: {"dcterms:provenance": 51}.get(term, 1))
    monkeypatch.setattr(cc.register_api, "literal_value", lambda pid, v: {"property_id": pid, "@value": v})
    monkeypatch.setattr(cc.register_api, "patch_item_rdf_values",
                        lambda s, b, c, item_id, updates, item_body=None: calls.update(item=item_id, updates=updates))

    def no_ledger_row(item_id, required=True):
        calls["required"] = required
        return None

    monkeypatch.setattr(cc, "_ledger_row_by_omeka_item_id", no_ledger_row)
    return calls


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["correct-catalog-item.py", *argv])
    cc.main()


def test_donor_becomes_provenance_on_an_item_without_ledger_row(omeka, monkeypatch) -> None:
    _main(monkeypatch, "--omeka-ref", "16396", "--donor", "Anthony Perkins, Brock")
    assert omeka["required"] is False
    assert omeka["updates"] == {"dcterms:provenance": [{"property_id": 51, "@value": "Anthony Perkins"},
                                                       {"property_id": 51, "@value": "Brock"}]}


def test_clear_donor(omeka, monkeypatch) -> None:
    _main(monkeypatch, "--omeka-ref", "16396", "--clear-donor")
    assert omeka["updates"] == {"dcterms:provenance": []}


def test_donor_and_clear_donor_together_is_refused(omeka, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="not both"):
        _main(monkeypatch, "--omeka-ref", "1", "--donor", "A", "--clear-donor")


def test_title_change_without_ledger_row_skips_the_bag(omeka, monkeypatch, capsys) -> None:
    _main(monkeypatch, "--omeka-ref", "16396", "--title", "New title")
    assert "no package on Wasabi" in capsys.readouterr().err


def test_removing_moby_match_still_needs_the_ledger(omeka, monkeypatch) -> None:
    monkeypatch.setattr(cc, "_ledger_row_by_omeka_item_id", lambda item_id, required=True: (_ for _ in ()).throw(
        SystemExit("No ledger row")) if required else None)
    with pytest.raises(SystemExit, match="No ledger row"):
        _main(monkeypatch, "--omeka-ref", "16396", "--clear-moby-url")
