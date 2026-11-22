"""scripts/compose_shared.sh: `compose run` never recreates the shared services (postgres, clamav).

A `docker compose run` without --no-deps recreates a dependency whenever its config hash differs,
and Compose versions compute that hash differently. On 2026-11-21 the host moved to Compose 5.5
while the Windmill worker kept 2.26, so each side recreated clamav whenever it ran, killing the
other side's virus scan.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "scripts" / "compose_shared.sh"
COMMON = ROOT / "scripts" / "remote-intake" / "common.sh"
HITSAVE_ENV = ROOT / "windmill_ops" / "lib" / "hitsave-env.sh"


def _run(body: str, *, up_fails: bool = False) -> tuple[list[str], str, int]:
    """Run ``body`` with a fake `docker` that logs its arguments; return (calls, stdout, exit)."""
    script = f"""
set -euo pipefail
docker() {{
  printf '%s\\n' "$*" >> "$CALLS"
  if [[ " $* " == *" up "* ]]; then echo "Container x Running" >&2; {"return 3" if up_fails else "return 0"}; fi
  echo "out:$*"
}}
{body}
"""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile("r", suffix=".log") as calls:
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={**os.environ, "CALLS": calls.name})
        return [line for line in Path(calls.name).read_text().splitlines()], p.stdout, p.returncode


def test_run_starts_shared_services_without_recreating_then_skips_deps():
    calls, out, code = _run(f'source "{SHARED}"; hitsave_compose docker compose -f a.yml -- run --rm ingest-worker x')
    assert code == 0
    assert calls == ["compose -f a.yml up -d --no-recreate --wait postgres clamav",
                     "compose -f a.yml run --no-deps --rm ingest-worker x"]
    assert out.strip() == "out:compose -f a.yml run --no-deps --rm ingest-worker x"  # nothing else on stdout


def test_other_subcommands_pass_through():
    calls, _, code = _run(f'source "{SHARED}"; hitsave_compose docker compose -- config --hash clamav')
    assert code == 0 and calls == ["compose config --hash clamav"]


def test_failed_up_stops_before_run():
    calls, out, code = _run(f'source "{SHARED}"; hitsave_compose docker compose -- run --rm x', up_fails=True)
    assert code != 0 and len(calls) == 1 and out == ""


def test_compose_run_helper_for_host_scripts():
    calls, _, code = _run(f'source "{SHARED}"; compose_run --rm -T ingest-worker cfg.yml')
    assert code == 0 and calls[-1] == "compose run --no-deps --rm -T ingest-worker cfg.yml"


def test_remote_intake_wrapper_uses_it(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    body = f"""
source "{COMMON}"
remote_intake_archiver_compose_dir() {{ echo "{tmp_path}"; }}
remote_intake_private_config_host() {{ echo /priv; }}
unset HITSAVE_LEDGER_REMOTE_HOST
remote_intake_archiver_compose run --rm -T ingest-worker y
"""
    calls, _, code = _run(body)
    assert code == 0
    assert calls[0].endswith("up -d --no-recreate --wait postgres clamav")
    assert calls[1].endswith("run --no-deps --rm -T ingest-worker y")


def test_windmill_wrapper_uses_it(tmp_path):
    (tmp_path / "config").mkdir()
    body = f"""
export ARCHIVER_ROOT="{ROOT}" HOST_ENV_FILE=/nonexistent WORKER_PATHS_FILE=/nonexistent
source "{HITSAVE_ENV}"
ARCHIVER_HOST_PATH="{tmp_path}"
unset HITSAVE_LEDGER_REMOTE_HOST
archiver_compose run --rm omeka-catalog z
"""
    calls, _, code = _run(body)
    assert code == 0, calls
    assert calls[0] == f"compose --project-directory {tmp_path} up -d --no-recreate --wait postgres clamav"
    assert calls[1] == f"compose --project-directory {tmp_path} run --no-deps --rm omeka-catalog z"


def test_host_scripts_never_run_compose_with_deps():
    """Every one-off `docker compose run` in the host scripts goes through compose_run."""
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.sh")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "docker compose run" in line and "--no-deps" not in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{n}")
    assert offenders == []
