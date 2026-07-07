#!/usr/bin/env python3
"""Queue role-bound WFG subagent tasks in the DB-first workflow queue.

Phase 4 implementation: skills are SOPs, subagents are workers. This wrapper
creates durable workflow_tasks rows with role, inputs, outputs, boundaries, and
next gate. Kanban remains an optional mirror handled elsewhere.
"""
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
SUBAGENTS = PROJECT / "config" / "subagents.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def ensure_schema(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_tasks(
          task_id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          opportunity_folder TEXT,
          role_id TEXT,
          task_type TEXT NOT NULL,
          current_state TEXT NOT NULL DEFAULT 'queued',
          priority INTEGER DEFAULT 0,
          due_at TEXT,
          input_json TEXT,
          output_json TEXT,
          idempotency_key TEXT UNIQUE,
          created_at TEXT NOT NULL,
          started_at TEXT,
          heartbeat_at TEXT,
          finished_at TEXT,
          error TEXT,
          next_gate TEXT,
          kanban_task_id TEXT,
          kanban_mirror_status TEXT DEFAULT 'not_attempted',
          environment TEXT DEFAULT 'production'
        );
        CREATE TABLE IF NOT EXISTS workflow_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          event_type TEXT,
          event_at TEXT,
          actor TEXT,
          details_json TEXT
        );
        """
    )


def load_roles(config_path: Path = SUBAGENTS) -> dict[str, dict[str, Any]]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    roles = {r["id"]: r for r in data.get("subagents", [])}
    return roles


def role_task_brief(role: dict[str, Any], task: dict[str, Any]) -> str:
    skills = ", ".join(role.get("primary_skills") or []) or "none"
    sop_refs = ", ".join(role.get("sop_refs") or []) or "none"
    return "\n".join([
        f"# WFG Delegated Task - {task['task_type']}",
        "",
        f"- Task ID: {task['task_id']}",
        f"- Role ID: {role['id']}",
        f"- Role mission: {role.get('role', '')}",
        f"- MVDE active: {role.get('mvde_active')}",
        f"- Primary skills/SOPs: {skills}",
        f"- SOP refs: {sop_refs}",
        f"- External-action boundary: {role.get('external_actions', 'none')}",
        f"- Dedupe key: {task.get('dedupe_key') or ''}",
        f"- Opportunity folder: `{task.get('opportunity_folder') or ''}`",
        f"- Next gate: {task.get('next_gate') or 'none'}",
        "",
        "## Inputs",
        "",
        "```json",
        json.dumps(task.get("input") or {}, indent=2, sort_keys=True),
        "```",
        "",
        "## Required Output Contract",
        "",
        "- State the role ID and task type.",
        "- List source files read.",
        "- List assumptions, risks, blockers, and next gate.",
        "- Save outputs under the opportunity folder when one exists.",
        "- Do not perform external action unless a downstream approval gate authorizes the exact action/version.",
        "",
    ])


def queue_task(
    *,
    role_id: str,
    task_type: str,
    dedupe_key: str = "",
    opportunity_folder: str = "",
    inputs: dict[str, Any] | None = None,
    next_gate: str = "",
    priority: int = 0,
    due_at: str = "",
    idempotency_key: str = "",
    config_path: Path = SUBAGENTS,
) -> dict[str, Any]:
    roles = load_roles(config_path)
    if role_id not in roles:
        raise ValueError(f"unknown role_id {role_id!r}")
    role = roles[role_id]
    if not task_type or task_type in set(role.get("primary_skills") or []):
        raise ValueError("task_type must describe work; do not queue a skill name as if it were a worker")
    key = idempotency_key or f"delegate:{dedupe_key}:{role_id}:{task_type}:{json.dumps(inputs or {}, sort_keys=True)}"
    with con() as c:
        ensure_schema(c)
        cur = c.execute(
            """insert or ignore into workflow_tasks(
                 dedupe_key, opportunity_folder, role_id, task_type, current_state,
                 priority, due_at, input_json, idempotency_key, created_at, next_gate, environment)
               values(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dedupe_key,
                opportunity_folder,
                role_id,
                task_type,
                "queued",
                priority,
                due_at,
                json.dumps({"role": role, "inputs": inputs or {}}, sort_keys=True),
                key,
                utc_now(),
                next_gate,
                os.environ.get("WFG_ENV", "production"),
            ),
        )
        if cur.rowcount:
            task_id = int(cur.lastrowid)
            created = True
        else:
            row = c.execute("select task_id from workflow_tasks where idempotency_key=?", (key,)).fetchone()
            task_id = int(row["task_id"])
            created = False
        task = {
            "task_id": task_id,
            "dedupe_key": dedupe_key,
            "opportunity_folder": opportunity_folder,
            "role_id": role_id,
            "task_type": task_type,
            "next_gate": next_gate,
            "input": inputs or {},
        }
        brief_path = None
        if opportunity_folder:
            brief_dir = Path(opportunity_folder) / "delegated_tasks"
            brief_dir.mkdir(parents=True, exist_ok=True)
            brief_path = brief_dir / f"task-{task_id:06d}-{role_id}-{task_type}.md"
            brief_path.write_text(role_task_brief(role, task), encoding="utf-8")
            c.execute("update workflow_tasks set output_json=? where task_id=?", (json.dumps({"task_brief": str(brief_path)}, sort_keys=True), task_id))
        c.execute(
            "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
            (dedupe_key, "subagent_task_queued", utc_now(), "wfg_delegate_task", json.dumps({"task_id": task_id, "role_id": role_id, "task_type": task_type, "created": created, "brief_path": str(brief_path) if brief_path else ""}, sort_keys=True)),
        )
        c.commit()
    return {"ok": True, "created": created, "task_id": task_id, "idempotency_key": key, "role_id": role_id, "task_type": task_type, "task_brief": str(brief_path) if brief_path else ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("roles")
    q = sub.add_parser("queue")
    q.add_argument("--role-id", required=True)
    q.add_argument("--task-type", required=True)
    q.add_argument("--dedupe-key", default="")
    q.add_argument("--opportunity-folder", default="")
    q.add_argument("--next-gate", default="")
    q.add_argument("--priority", type=int, default=0)
    q.add_argument("--due-at", default="")
    q.add_argument("--input-json", default="{}")
    args = ap.parse_args()
    if args.cmd == "roles":
        print(json.dumps(load_roles(), indent=2, sort_keys=True))
        return 0
    if args.cmd == "queue":
        inputs = json.loads(args.input_json or "{}")
        print(json.dumps(queue_task(role_id=args.role_id, task_type=args.task_type, dedupe_key=args.dedupe_key, opportunity_folder=args.opportunity_folder, inputs=inputs, next_gate=args.next_gate, priority=args.priority, due_at=args.due_at), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
