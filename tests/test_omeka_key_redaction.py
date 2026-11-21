"""The Omeka API key must not reach logs (Omeka S only accepts it as URL query parameters)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from omeka_key_redaction import install_omeka_key_redaction, redact_omeka_keys  # noqa: E402

KEY_ID = "TestIdentity0123456789abcdefABCD"
KEY_CRED = "TestCredential0123456789abcdefAB"


def test_redacts_key_from_requests_error_message() -> None:
    message = (
        "HTTPSConnectionPool(host='archive.hitsave.org', port=443): Max retries exceeded with url: "
        f"/api/sites?key_identity={KEY_ID}&key_credential={KEY_CRED}&slug=start "
        "(Caused by NewConnectionError(...))"
    )
    out = redact_omeka_keys(message)
    assert KEY_ID not in out and KEY_CRED not in out
    assert "key_identity=REDACTED&key_credential=REDACTED&slug=start" in out


def test_redacts_key_from_params_dict_repr() -> None:
    out = redact_omeka_keys(str({"key_identity": KEY_ID, "key_credential": KEY_CRED, "page": 1}))
    assert KEY_ID not in out and KEY_CRED not in out
    assert "'page': 1" in out


def test_leaves_text_without_keys_alone() -> None:
    assert redact_omeka_keys("item 31474 not found") == "item 31474 not found"


def test_uncaught_exception_traceback_is_redacted(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_omeka_key_redaction()
    try:
        raise ConnectionError(f"url: /api/items?key_identity={KEY_ID}&key_credential={KEY_CRED}")
    except ConnectionError:
        sys.excepthook(*sys.exc_info())
    err = capsys.readouterr().err
    assert "ConnectionError" in err and "Traceback" in err
    assert KEY_ID not in err and KEY_CRED not in err


_OMEKA_MODULES = re.compile(r"omeka_catalog_common|omeka_staff_metadata|omeka_external_dip|omeka_dip_media|key_credential")


def test_every_omeka_script_installs_key_redaction() -> None:
    """A script that runs on its own and talks to Omeka must redact the key from its traceback."""
    missing = []
    for path in sorted((REPO / "scripts").glob("*.py")):
        text = path.read_text()
        if path.name == "omeka_key_redaction.py" or "__main__" not in text:
            continue
        if _OMEKA_MODULES.search(text) and "install_omeka_key_redaction()" not in text:
            missing.append(path.name)
    assert missing == []
