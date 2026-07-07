#!/usr/bin/env python3
"""Generate the WFG command center snapshot and Telegram-ready daily brief."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()
OUT = PROJECT / "state" / "command-center"
OBSIDIAN = PROJECT / "obsidian-vault" / "00-Dashboards"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def rows(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in c.execute(sql, params)]
    except sqlite3.OperationalError:
        return []


def table_exists(c: sqlite3.Connection, table: str) -> bool:
    try:
        return bool(c.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone())
    except sqlite3.OperationalError:
        return False


def gather() -> dict[str, Any]:
    data = {
        "generated_at": utc_now(),
        "active_opportunities": [],
        "waiting_approvals": [],
        "due_dates": [],
        "subcontractor_responses": [],
        "missing_info": [],
        "risks": [],
        "next_recommended_actions": [],
        "money_pipeline": [],
        "answers": {},
    }
    if not DB.exists():
        data["risks"].append({"risk": "workflow_db_missing", "detail": str(DB)})
        return data
    c = con()
    try:
        data["active_opportunities"] = rows(c, """
            select dedupe_key, title, agency, solicitation_number, response_deadline,
                   workflow_status stage, '' owner_subagent,
                   case when response_deadline is not null then 'deadline_review' else 'normal' end risk_level
              from opportunities
             where coalesce(workflow_status,'') not like 'closed_%'
             order by datetime(coalesce(response_deadline,'2999-01-01')) asc
             limit 100
        """)
        data["waiting_approvals"] = rows(c, """
            select approval_id, gate_id, gate, dedupe_key, requested_at, record_path,
                   exact_action, artifact_version, artifact_hash, decision
              from approvals
             where decision in ('pending','draft','held','revise_requested') and coalesce(valid,1)=1
             order by datetime(coalesce(requested_at,'1970-01-01')) asc
             limit 100
        """)
        data["due_dates"] = [
            {"dedupe_key": x.get("dedupe_key"), "kind": "government_deadline", "due_at": x.get("response_deadline"), "title": x.get("title")}
            for x in data["active_opportunities"] if x.get("response_deadline")
        ]
        data["subcontractor_responses"] = rows(c, """
            select dedupe_key,
                   sum(case when direction='outbound' then 1 else 0 end) sent_count,
                   sum(case when direction='inbound' then 1 else 0 end) replies,
                   sum(case when lower(coalesce(status,'')) like '%quote%' then 1 else 0 end) quotes_received,
                   sum(case when lower(coalesce(status,'')) like '%no-bid%' then 1 else 0 end) no_bids,
                   max(occurred_at) last_activity
              from subcontractor_interactions
             group by dedupe_key
             order by datetime(coalesce(max(occurred_at),'1970-01-01')) desc
             limit 100
        """)
        data["missing_info"] = rows(c, """
            select dedupe_key, artifact_type, local_path, created_at
              from artifact_index
             where audience='wfg_internal' and lower(local_path) like '%missing%'
             order by datetime(created_at) desc limit 50
        """)
        if table_exists(c, "workflow_tasks"):
            blockers = rows(c, """
                select dedupe_key, role_id, task_type, current_state, error, next_gate, created_at
                  from workflow_tasks
                 where current_state in ('blocked','failed_retryable','failed_terminal','waiting_approval')
                 order by datetime(created_at) desc limit 100
            """)
            for b in blockers:
                data["risks"].append({"risk": b["current_state"], "dedupe_key": b.get("dedupe_key"), "detail": f"{b.get('role_id')} / {b.get('task_type')}: {b.get('error') or b.get('next_gate') or ''}"})
        if table_exists(c, "external_action_ledger"):
            duplicates = rows(c, """
                select dedupe_key, recipient_key, status, executed_at, proof_path
                  from external_action_ledger
                 where status in ('historical_sent_proof','executed') and needs_human_review=1
                 order by action_id desc limit 50
            """)
            for d in duplicates:
                data["risks"].append({"risk": "duplicate_or_review_contact", "dedupe_key": d.get("dedupe_key"), "detail": f"{d.get('recipient_key')} {d.get('status')}"})
        data["money_pipeline"] = rows(c, """
            select dedupe_key, pricing_version, total, output_path, created_at
              from pricing_versions order by id desc limit 50
        """)
    finally:
        c.close()
    data["next_recommended_actions"] = recommend(data)
    data["answers"] = {
        "what_is_hermes_working_on": [x.get("title") or x.get("dedupe_key") for x in data["active_opportunities"][:10]],
        "what_needs_approval": [x.get("approval_id") for x in data["waiting_approvals"][:10]],
        "what_is_due_soon": data["due_dates"][:10],
        "which_subcontractors_responded": data["subcontractor_responses"][:10],
        "what_is_blocked": data["risks"][:10],
        "what_should_i_do_next": data["next_recommended_actions"][:10],
    }
    return data


def recommend(data: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for a in data["waiting_approvals"][:10]:
        actions.append({"priority": "high", "action": "review_approval", "approval_id": a.get("approval_id"), "gate_id": a.get("gate_id"), "path": a.get("record_path")})
    for r in data["risks"][:10]:
        actions.append({"priority": "medium", "action": "resolve_blocker", "dedupe_key": r.get("dedupe_key"), "detail": r.get("detail")})
    if not actions:
        actions.append({"priority": "normal", "action": "run_intake_or_select_next_opportunity", "detail": "No pending approvals or blockers in the local snapshot."})
    return actions


def markdown(data: dict[str, Any]) -> str:
    lines = [
        "# WFG Command Center",
        "",
        f"Generated at: {data['generated_at']}",
        "",
        "## What Needs Approval",
    ]
    if data["waiting_approvals"]:
        for a in data["waiting_approvals"][:20]:
            lines.append(f"- {a.get('gate_id') or a.get('gate')}: {a.get('approval_id')} - `{a.get('record_path')}`")
    else:
        lines.append("- None.")
    lines += ["", "## Due Soon"]
    for d in data["due_dates"][:20]:
        lines.append(f"- {d.get('due_at')}: {d.get('title') or d.get('dedupe_key')}")
    if not data["due_dates"]:
        lines.append("- No due dates found.")
    lines += ["", "## Risks / Blockers"]
    for r in data["risks"][:20]:
        lines.append(f"- {r.get('risk')}: {r.get('dedupe_key') or ''} {r.get('detail') or ''}")
    if not data["risks"]:
        lines.append("- None found.")
    lines += ["", "## Next Recommended Actions"]
    for a in data["next_recommended_actions"][:20]:
        lines.append(f"- [{a.get('priority')}] {a.get('action')}: {a.get('approval_id') or a.get('dedupe_key') or a.get('detail') or ''}")
    lines += ["", "## Six Operator Questions", ""]
    for key, value in data["answers"].items():
        lines.append(f"### {key.replace('_', ' ').title()}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(value, indent=2, sort_keys=True, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def telegram_brief(data: dict[str, Any]) -> str:
    approvals = len(data["waiting_approvals"])
    blockers = len(data["risks"])
    due = len(data["due_dates"])
    lines = [f"WFG daily command brief ({data['generated_at']})", f"Approvals waiting: {approvals}", f"Blockers/risks: {blockers}", f"Due dates tracked: {due}", "", "Next:"]
    for action in data["next_recommended_actions"][:6]:
        lines.append(f"- {action.get('priority')}: {action.get('action')} {action.get('approval_id') or action.get('dedupe_key') or ''}".strip())
    return "\n".join(lines)


def build(out_dir: Path = OUT) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = gather()
    latest_json = out_dir / "latest.json"
    latest_md = out_dir / "WFG Command Center.md"
    brief = out_dir / "telegram_brief.txt"
    latest_json.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    latest_md.write_text(markdown(data), encoding="utf-8")
    brief.write_text(telegram_brief(data), encoding="utf-8")
    OBSIDIAN.mkdir(parents=True, exist_ok=True)
    (OBSIDIAN / "WFG Command Center.md").write_text(latest_md.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "json": str(latest_json), "markdown": str(latest_md), "telegram_brief": str(brief), "obsidian": str(OBSIDIAN / "WFG Command Center.md"), "answers": data["answers"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()
    print(json.dumps(build(args.out_dir), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
