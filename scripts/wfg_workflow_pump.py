#!/usr/bin/env python3
"""Single WFG workflow pump for approval-button driven handoffs.

This is the bridge between Nick's remote approvals and the next safe internal
workflow. It intentionally performs only internal work:
1. Reconcile Telegram approval buttons into the local approval/workflow DB.
2. Dispatch newly approved gates into Kanban/internal subagent tasks.
3. Optionally create Gmail drafts for approved outreach review.

It never sends emails, submits bids, signs, certifies, spends money, or contacts
anyone. Downstream work must create the next approval gate before external use.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run_step(name: str, cmd: list[str], *, timeout: int = 240, allow_failure: bool = False) -> dict[str, Any]:
    started = utc_now()
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = proc.stdout.strip()
        ok = proc.returncode == 0
        return {
            "name": name,
            "ok": ok,
            "returncode": proc.returncode,
            "started_at": started,
            "finished_at": utc_now(),
            "command": cmd,
            "output": out[-12000:],
            "error": "" if ok else out[-4000:],
            "allowed_failure": bool(allow_failure),
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "returncode": None,
            "started_at": started,
            "finished_at": utc_now(),
            "command": cmd,
            "output": "",
            "error": str(exc),
            "allowed_failure": bool(allow_failure),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-kanban-dispatch", action="store_true", help="Queue follow-on task but do not ask Hermes Kanban to dispatch it immediately.")
    ap.add_argument("--sync-email-drafts", action="store_true", help="Also create Gmail drafts from approved/local outreach drafts. Drafts only; never sends.")
    ap.add_argument("--json", action="store_true", help="Print JSON result only.")
    args = ap.parse_args()

    if not PROJECT.exists():
        print(json.dumps({"ok": False, "error": f"WFG_PROJECT_DIR not found: {PROJECT}"}, indent=2))
        return 2

    steps: list[dict[str, Any]] = []
    steps.append(run_step("reconcile_approval_buttons", [sys.executable, "scripts/reconcile_wfg_approval_buttons.py"], timeout=180, allow_failure=False))

    dispatch_cmd = [sys.executable, "scripts/wfg_approval_dispatcher.py"]
    if args.no_kanban_dispatch:
        dispatch_cmd.append("--no-kanban-dispatch")
    steps.append(run_step("dispatch_approved_gates", dispatch_cmd, timeout=300, allow_failure=False))

    if args.sync_email_drafts:
        # Creating a Gmail draft is still non-binding, but it can clutter Gmail.
        # Keep it opt-in until the outreach workflow has fully stabilized.
        steps.append(run_step("sync_gmail_drafts", [sys.executable, "scripts/wfg_email_draft_sync.py"], timeout=300, allow_failure=True))

    ok = all(step["ok"] or step.get("allowed_failure") for step in steps)
    result = {"ok": ok, "project": str(PROJECT), "ran_at": utc_now(), "steps": steps}

    log_dir = PROJECT / "state" / "workflow-pump-runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{utc_now().replace(':','').replace('+','Z')}.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
