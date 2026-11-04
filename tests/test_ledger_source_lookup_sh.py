"""remote_intake_ledger_source_for_slug must keep spaces inside the value (Wasabi prefixes)."""
from __future__ import annotations

import subprocess
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "scripts" / "remote-intake" / "common.sh"


def _lookup(fake_psql_output: str) -> str:
    script = f"""
set -euo pipefail
source "{COMMON}"
remote_intake_archiver_compose() {{ printf '%b' {fake_psql_output!r}; }}
printf '[%s]' "$(remote_intake_ledger_source_for_slug redump-e3-2002)"
"""
    return subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True).stdout


def test_keeps_inner_spaces() -> None:
    assert _lookup(" wasabi:Weekly Famitsu/E3_2002 \n") == "[wasabi:Weekly Famitsu/E3_2002]"


def test_drive_id_and_empty() -> None:
    assert _lookup("drive:1AbC-_x\n") == "[drive:1AbC-_x]"
    assert _lookup("") == "[]"
