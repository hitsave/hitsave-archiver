"""Keep the Omeka API key out of logs.

Omeka S accepts the API key only as the URL query parameters key_identity and key_credential,
so a requests error that quotes the URL also quotes the key. Scripts that talk to Omeka call
install_omeka_key_redaction() before main(), and anything that stores an error message passes
it through redact_omeka_keys() first.
"""

from __future__ import annotations

import re
import sys
import traceback

# key_identity=VALUE in a URL, and 'key_identity': 'VALUE' in a printed params dict.
_IN_URL = re.compile(r"(key_identity|key_credential)=[^&\s'\"]+")
_IN_DICT = re.compile(r"""(['"]key_(?:identity|credential)['"]\s*:\s*['"])[^'"]*""")


def redact_omeka_keys(text: str) -> str:
    """The text with every Omeka key value replaced by REDACTED."""
    return _IN_DICT.sub(r"\1REDACTED", _IN_URL.sub(r"\1=REDACTED", text))


def install_omeka_key_redaction() -> None:
    """Print uncaught exceptions with the Omeka key redacted."""

    def hook(exc_type, exc, tb) -> None:
        sys.stderr.write(redact_omeka_keys("".join(traceback.format_exception(exc_type, exc, tb))))

    sys.excepthook = hook
