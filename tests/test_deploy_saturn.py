"""Deploy on merge: which changes trigger which deploy step, and when the poller deploys."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy" / "deploy-saturn.sh"
POLL = ROOT / "scripts" / "deploy" / "deploy-poll.sh"
sys.path.insert(0, str(ROOT / "scripts" / "deploy"))

import ci_status  # noqa: E402


def plan(*paths: str) -> list[str]:
    out = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY}"; deploy_plan'],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def test_image_inputs_trigger_a_rebuild() -> None:
    assert plan("scripts/remote-intake/common.sh") == ["images"]
    assert plan("ingest-worker/Dockerfile") == ["images"]
    assert plan("packages/hitsave-dump-sidecars/src/hitsave_dump_sidecars/redump.py") == ["images"]
    assert plan("docker-compose.yml") == ["images"]


def test_job_files_trigger_a_windmill_update() -> None:
    assert plan("windmill_ops/wmill/f/hitsave/remote_intake_sweep.sh") == ["windmill"]
    assert plan("windmill_ops/wmill/f/hitsave/ledger_backup.schedule.yaml") == ["windmill"]


def test_ledger_inputs_trigger_a_ledger_deploy() -> None:
    # The same paths that make the ledger-image workflow build a new image.
    assert plan("ledger/app.py") == ["ledger"]
    assert plan("config/ledger.yaml.example") == ["ledger"]
    assert plan(".dockerignore") == ["ledger"]
    assert plan("scripts/omeka_catalog_common.py") == ["images", "ledger"]


def test_scripts_in_subfolders_do_not_touch_the_ledger() -> None:
    assert plan("scripts/remote-intake/sweep.py") == ["images"]


def test_all_steps_when_everything_changes() -> None:
    assert plan("scripts/ingest-folder.py", "windmill_ops/wmill/f/hitsave/ledger_backup.sh") == [
        "images",
        "windmill",
        "ledger",
    ]


def test_docs_and_tests_need_no_step() -> None:
    assert plan("docs/archivist-guide.md", "tests/test_x.py", "README.md") == []


def runs(*pairs: tuple[str, str | None]) -> dict:
    return {
        "total_count": len(pairs),
        "check_runs": [
            {"name": f"job{i}", "status": status, "conclusion": conclusion}
            for i, (status, conclusion) in enumerate(pairs)
        ],
    }


def test_ci_passes_when_every_check_succeeded_or_was_skipped() -> None:
    assert ci_status.verdict(runs(("completed", "success"), ("completed", "skipped"))) == "passed"


def test_ci_waits_while_a_check_is_running() -> None:
    assert ci_status.verdict(runs(("completed", "success"), ("in_progress", None))) == "pending"
    assert ci_status.verdict(runs(("queued", None))) == "pending"


def test_ci_fails_when_any_check_failed() -> None:
    assert ci_status.verdict(runs(("completed", "success"), ("completed", "failure"))) == "failed"
    assert ci_status.verdict(runs(("completed", "cancelled"))) == "failed"
    assert ci_status.verdict(runs(("completed", "timed_out"))) == "failed"


def test_ci_reports_none_when_no_checks_ran() -> None:
    assert ci_status.verdict({"total_count": 0, "check_runs": []}) == "none"


def poll(tmp_path: Path, verdict: str, *, deployed: str, target: str, extra: dict | None = None):
    """Run deploy-poll.sh with git, curl and the deploy script replaced by fakes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "git").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"rev-parse origin/main"*) echo {target} ;;\n'
        f'  *"rev-parse HEAD"*) echo {deployed} ;;\n'
        '  *"show "*) printf \'#!/bin/sh\\necho "deploy $1" >> "$FAKE_CALLS"\\n\' ;;\n'
        "esac\n"
    )
    (bin_dir / "curl").write_text(f"#!/bin/sh\necho {verdict}\n")
    for f in ("git", "curl"):
        (bin_dir / f).chmod(0o755)
    state = tmp_path / "checkout" / ".generated" / "deploy"
    state.mkdir(parents=True, exist_ok=True)
    (state / "deployed-commit").write_text(deployed + "\n")
    calls = tmp_path / "calls"
    calls.write_text("")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HITSAVE_ARCHIVER_DIR": str(tmp_path / "checkout"),
        # The fake curl prints the verdict itself, so the reader only passes it on.
        "HITSAVE_DEPLOY_CI_STATUS": "cat",
        "FAKE_CALLS": str(calls),
    }
    env.update(extra or {})
    result = subprocess.run(["bash", str(POLL)], env=env, capture_output=True, text=True)
    return result, calls.read_text(), state


def test_poller_deploys_a_new_commit_once_ci_passed(tmp_path) -> None:
    result, calls, _ = poll(tmp_path, "passed", deployed="aaa", target="bbb")
    assert result.returncode == 0, result.stderr
    assert calls == "deploy bbb\n"


def test_poller_does_nothing_when_main_has_not_moved(tmp_path) -> None:
    result, calls, _ = poll(tmp_path, "passed", deployed="bbb", target="bbb")
    assert result.returncode == 0
    assert calls == ""


def test_poller_waits_for_running_checks(tmp_path) -> None:
    result, calls, _ = poll(tmp_path, "pending", deployed="aaa", target="bbb")
    assert result.returncode == 0
    assert calls == ""


def test_poller_never_deploys_a_commit_whose_checks_failed(tmp_path) -> None:
    result, calls, state = poll(tmp_path, "failed", deployed="aaa", target="bbb")
    assert calls == ""
    assert (state / "failed-commit").read_text().strip() == "bbb"


def test_poller_skips_a_commit_that_already_failed_to_deploy(tmp_path) -> None:
    state = tmp_path / "checkout" / ".generated" / "deploy"
    state.mkdir(parents=True)
    (state / "failed-commit").write_text("bbb\n")
    result, calls, _ = poll(tmp_path, "passed", deployed="aaa", target="bbb")
    assert result.returncode == 0
    assert calls == ""


def test_poller_gives_checks_time_to_start(tmp_path) -> None:
    # Right after a push GitHub may not have created any checks yet.
    result, calls, _ = poll(tmp_path, "none", deployed="aaa", target="bbb")
    assert calls == ""
    result, calls, _ = poll(
        tmp_path, "none", deployed="aaa", target="bbb", extra={"HITSAVE_DEPLOY_NO_CHECKS_WAIT_SECONDS": "0"}
    )
    assert calls == "deploy bbb\n"
