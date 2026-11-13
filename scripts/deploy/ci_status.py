#!/usr/bin/env python3
"""Read GitHub's check runs for one commit (JSON on stdin) and print one word:

  passed   every check finished and none failed (skipped and neutral count as passed)
  pending  a check is still queued or running
  failed   a check failed, was cancelled or timed out
  none     the commit has no checks (yet)

deploy-poll.sh feeds it the output of
`gh api repos/hitsave/hitsave-archiver/commits/<commit>/check-runs?per_page=100`.
"""
from __future__ import annotations

import json
import sys
from typing import Any

OK_CONCLUSIONS = {"success", "skipped", "neutral"}


def verdict(payload: dict[str, Any]) -> str:
    runs = payload.get("check_runs") or []
    if not runs:
        return "none"
    if any(run.get("status") != "completed" for run in runs):
        return "pending"
    if any(run.get("conclusion") not in OK_CONCLUSIONS for run in runs):
        return "failed"
    return "passed"


if __name__ == "__main__":
    print(verdict(json.load(sys.stdin)))
