#!/usr/bin/env python3
"""Dispatch WFG downstream work after an approval gate is accepted.

This is the event bridge Nick wants for the WFG pipeline:

    task runs -> approval gate -> Nick approves -> next non-binding task starts

Safety boundary: this dispatcher only queues/starts internal follow-on work. It
must not send subcontractor emails, submit bids, approve prices, or perform any
external/binding action. Downstream workers must create the next approval gate
before external outreach, final pricing, or submission.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()
ROUTING = PROJECT / "config" / "approval-routing.json"
ENV_PATHS = [Path.home() / ".hermes" / ".env", PROJECT / ".env"]
BOARD = os.environ.get("WFG_KANBAN_BOARD", "gov-contracting")
DEFAULT_ASSIGNEE = os.environ.get("WFG_APPROVAL_NEXT_ASSIGNEE", "default")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_gates  # noqa: E402
import wfg_tracking_schema  # noqa: E402


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def migrate() -> None:
    with con() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_dispatches(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              approval_id TEXT NOT NULL,
              dedupe_key TEXT NOT NULL,
              gate TEXT NOT NULL,
              decision TEXT NOT NULL,
              dispatch_type TEXT NOT NULL,
              status TEXT NOT NULL,
              task_id TEXT,
              task_title TEXT,
              task_body TEXT,
              created_at TEXT NOT NULL,
              dispatched_at TEXT,
              dispatch_output TEXT,
              error TEXT,
              UNIQUE(approval_id, dispatch_type)
            );
            """
        )
        wfg_tracking_schema.ensure_phase2_workflow_schema(c)
        c.commit()


def event(dedupe_key: str, event_type: str, details: dict[str, Any] | None = None) -> None:
    with con() as c:
        c.execute(
            "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
            (dedupe_key, event_type, now(), "wfg_approval_dispatcher", json.dumps(details or {}, sort_keys=True)),
        )
        c.commit()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for p in ENV_PATHS:
        if not p.exists():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def load_route(kind: str) -> dict[str, Any] | None:
    if not ROUTING.exists():
        return None
    try:
        data = json.loads(ROUTING.read_text(errors="ignore"))
        routes = data.get("routes") or {}
        route = routes.get(kind)
        if isinstance(route, dict) and route.get("status") == "verified" and route.get("target"):
            return route
    except Exception:
        return None
    return None


def send_telegram(target: str, text: str) -> dict[str, Any]:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"}
    m = re.match(r"^telegram:([^:]+)(?::([^:]+))?$", target)
    if not m:
        return {"ok": False, "error": f"unsupported target {target!r}"}
    chat_id, thread_id = m.group(1), m.group(2)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def opportunity_for_key(dedupe_key: str) -> dict[str, Any]:
    with con() as c:
        row = c.execute("select * from opportunities where dedupe_key=?", (dedupe_key,)).fetchone()
    return row_dict(row) or {"dedupe_key": dedupe_key, "title": dedupe_key}


def folder_for_approval(record_path: str) -> str:
    if record_path:
        p = Path(record_path)
        # .../<opportunity>/approvals/<packet>.md -> opportunity folder
        if p.parent.name == "approvals":
            return str(p.parent.parent)
    return ""


def downstream_for_approval(approval: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve the follow-on internal work for an approved gate.

    Matches on gate_id only (consensus plan Section 5 sequencing constraint).
    Returns (downstream, refusal): exactly one is non-None for approved
    decisions; both are None for non-approved decisions.
    """
    decision = approval.get("decision") or ""
    if decision != "approved":
        return None, None
    gate_id = wfg_gates.resolve_gate_id(approval)
    if gate_id is None:
        return None, {
            "reason": "unknown_gate_id",
            "detail": f"approval has no known gate_id and gate text {approval.get('gate')!r} is not in the exact legacy map; refusing to guess",
        }
    entry = wfg_gates.GATES[gate_id]
    dispatch = entry.get("dispatch")
    if not dispatch:
        return None, {
            "reason": "gate_not_dispatchable",
            "detail": entry.get("no_dispatch_reason") or f"{gate_id} has no automatic downstream work",
            "gate_id": gate_id,
        }
    next_gate_id = dispatch.get("next_gate_id")
    return {
        "gate_id": gate_id,
        "dispatch_type": dispatch["dispatch_type"],
        "route": dispatch["route"],
        "status_after_queue": dispatch.get("status_after_queue"),
        "title_prefix": dispatch["title_prefix"],
        "next_gate": wfg_gates.gate_display_name(next_gate_id) if next_gate_id else "none — terminal gate",
    }, None


def task_body(approval: dict[str, Any], opp: dict[str, Any], downstream: dict[str, Any]) -> str:
    folder = folder_for_approval(approval.get("record_path") or "")
    title = opp.get("title") or approval.get("dedupe_key")
    notice_id = opp.get("notice_id") or approval.get("dedupe_key", "").removeprefix("notice:")
    solicitation = opp.get("solicitation_number") or ""
    gate = approval.get("gate") or ""
    record_path = approval.get("record_path") or ""
    approved_at = approval.get("decided_at") or ""
    dispatch_type = downstream["dispatch_type"]

    if dispatch_type == "gate1_subcontractor_sourcing":
        specific = """
Start the internal subcontractor-sourcing step for this approved pursue decision:
1. Read the opportunity folder, extracted solicitation/PWS text, sourcing criteria, missing-info file, and risk register.
2. Identify the actual trade(s), place of performance, due date, special credentials, base access, licensing, insurance, environmental/safety constraints, and any reasons outreach may be impractical.
3. Search the internal subcontractor CRM first, then public/local sources only as needed.
4. Save a candidate list and evidence under the opportunity folder, preferably `scope_sheets/subcontractor_candidates.csv` plus supporting evidence JSON/markdown.
5. Build the subcontractor-facing bid packet using the integrated dynamic DOCX system. Run:
   `python3 scripts/wfg_sub_bid_packet.py "{folder}" --docx --drive`
   If Drive credentials/root folder are not configured, run the same command without `--drive` and flag Drive setup in the internal review summary.
6. Draft subcontractor-facing quote request text. The outreach draft should link or attach only the approved subcontractor packet, not the internal review files.
7. Create one GATE_2_PACKAGE approval packet covering all three components — packet (version/hash), exact recipient list, and exact message text — with local file paths, Google Drive review links when available, and the internal review summary. The packet must include a `Gate ID: GATE_2_PACKAGE` line. Send the approval packet with `scripts/send_wfg_approval_buttons.py`.
8. If there are viable recipients but critical packet facts are missing, create a blocker/reconsideration note instead of contacting anyone.
9. If the opportunity should not proceed despite Gate 1 approval, create a clear blocker/reconsideration note instead of contacting anyone.
""".strip()
    elif dispatch_type == "gate2_send_approval_prep":
        specific = """
The outreach package (packet + recipients + message) is approved. Prepare the GATE_2_SEND approval packet:
1. Verify the approved packet hash, recipient list, and message hash are unchanged; if anything changed, create a new GATE_2_PACKAGE cycle instead.
2. Run the duplicate check against external_action_ledger and subcontractor_interactions for every recipient; disclose any prior contact with dates in the packet.
3. Create the GATE_2_SEND approval packet (must include `Gate ID: GATE_2_SEND`) referencing the GATE_2_PACKAGE approval ID and all hashes, and send it with `scripts/send_wfg_approval_buttons.py`.
Do not send anything to anyone at this step.
""".strip()
    elif dispatch_type == "gate2_outreach_execution":
        specific = """
Execute only the exact outreach package that was approved by GATE_2_SEND. Before each send, check external_action_ledger for the opportunity/recipient pair and stop on any prior contact. Verify Gmail sent-message metadata or form-confirmation evidence. Record proof in external_action_ledger, subcontractor_interactions, and the opportunity folder. Do not change recipients/message/packet without a new GATE_2_PACKAGE cycle.
""".strip()
    elif dispatch_type == "gate3_followup_execution":
        specific = """
Send only the exact approved follow-up text to the exact approved recipients after a ledger duplicate check. Record proof like any other external action. No scope changes, commitments, or price discussion.
""".strip()
    elif dispatch_type == "gate3_proposal_package":
        specific = """
Assemble proposal/pricing package from the exact approved price basis. Do not submit. Prepare the GATE_4_PACKAGE approval packet (must include `Gate ID: GATE_4_PACKAGE`) with all files Nick must review.
""".strip()
    elif dispatch_type == "gate5_submission_proof_tracking":
        specific = """
Gate 5 is approved: the authorized human submits. Track that submission proof (portal confirmation, sent-email proof, package hash) is archived under `09 Submission Proof`/the opportunity folder. Only after proof exists may the opportunity state become submitted_by_human. Hermes never submits.
""".strip()
    elif dispatch_type == "gate6_closeout":
        specific = """
Archive/closeout approved: update the CRM, decision log, and dashboard archive; record win/loss/debrief notes. Never delete audit records.
""".strip()
    elif dispatch_type == "amend_resume":
        specific = """
Amendment review approved. Resume internal work from the stage named in the approval. Re-issue any approvals voided by the amendment as new versions; never reuse a voided approval.
""".strip()
    else:
        specific = """
Prepare a human submission handoff/proof checklist only. Do not submit automatically. Record any human-submission proof only after it exists.
""".strip()

    if folder:
        specific = specific.replace("{folder}", folder)
    else:
        specific = specific.replace("{folder}", "/path/to/opportunity-folder")

    return f"""Automated WFG approval-chain task.

Opportunity: {title}
Notice ID: {notice_id}
Solicitation: {solicitation}
Dedupe key: {approval.get('dedupe_key')}
Current approved gate: {gate}
Approval ID: {approval.get('approval_id')}
Approved by: {approval.get('approver') or 'unknown'} at {approved_at}
Approval packet: {record_path}
Opportunity folder: {folder}
Next gate expected: {downstream['next_gate']}

{specific}

Hard stop rules:
- Do NOT send external emails, submit web forms, contact subcontractors/agencies, approve final price, sign, certify, spend money, upload sensitive documents, or submit any bid unless the exact downstream approval gate authorizes that exact action/version.
- Save outputs in the opportunity folder and update the workflow database/events where possible.
- Post/route a concise status update or approval packet using the WFG approval coordinator conventions.
"""


def create_kanban_task(approval: dict[str, Any], downstream: dict[str, Any]) -> dict[str, Any]:
    opp = opportunity_for_key(approval["dedupe_key"])
    title = f"{downstream['title_prefix']}: {opp.get('title') or approval['dedupe_key']}"
    body = task_body(approval, opp, downstream)
    idem = f"wfg:{approval['approval_id']}:{downstream['dispatch_type']}"
    cmd = [
        "hermes", "kanban", "--board", BOARD, "create", title,
        "--body", body,
        "--assignee", DEFAULT_ASSIGNEE,
        "--workspace", f"dir:{PROJECT}",
        "--idempotency-key", idem,
        "--created-by", "wfg_approval_dispatcher",
        "--skill", "wfg-subcontractor-scout",
        "--skill", "wfg-outreach-drafter",
        "--skill", "wfg-subcontractor-bid-packet",
        "--skill", "wfg-approval-coordinator",
        "--max-runtime", "2h",
        "--goal",
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    out = proc.stdout.strip()
    if proc.returncode != 0:
        raise RuntimeError(out or f"kanban create exited {proc.returncode}")
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out}
    task_id = data.get("id") or data.get("task_id") or data.get("task", {}).get("id")
    return {"task_id": task_id, "title": title, "body": body, "raw": data, "output": out}


def dispatch_kanban() -> str:
    proc = subprocess.run(
        ["hermes", "kanban", "--board", BOARD, "dispatch"],
        cwd=PROJECT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    return proc.stdout.strip()


def approvals_to_dispatch(
    button_id: str | None = None,
    approval_id: str | None = None,
    *,
    recent_minutes: int | None = 30,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "a.decision='approved' and a.valid=1 and a.used_at is not null"
    if approval_id:
        where += " and a.approval_id=?"
        params.append(approval_id)
    elif not button_id and recent_minutes is not None:
        # Safety: a no-arg dispatcher pass behaves like an event bridge,
        # not a historical backfill of every old approval in the database.
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=recent_minutes)).isoformat(timespec="seconds")
        where += " and a.decided_at>=?"
        params.append(cutoff)
    rows: list[sqlite3.Row]
    with con() as c:
        if button_id:
            # Button ID lives in workflow event JSON; use LIKE as a robust SQLite-compatible filter.
            where += " and exists (select 1 from workflow_events e where e.dedupe_key=a.dedupe_key and e.event_type='approval_button_reconciled' and e.details_json like ?)"
            params.append(f'%"button_id": "{button_id}"%')
        rows = c.execute(
            f"""select a.* from approvals a
                 where {where}
                 order by datetime(a.decided_at) asc, a.id asc""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def already_dispatched(approval_id: str, dispatch_type: str) -> dict[str, Any] | None:
    with con() as c:
        r = c.execute(
            "select * from approval_dispatches where approval_id=? and dispatch_type=?",
            (approval_id, dispatch_type),
        ).fetchone()
    return row_dict(r)


def set_workflow_status_after_queue(approval: dict[str, Any], downstream: dict[str, Any]) -> None:
    """Advance the opportunity to the post-approval internal-work status.

    This is intentionally limited to internal workflow states. External actions
    remain gated by the next approval packet.
    """
    target = downstream.get("status_after_queue")
    if not target:
        return
    dedupe_key = approval["dedupe_key"]
    with con() as c:
        row = c.execute("select workflow_status from opportunities where dedupe_key=?", (dedupe_key,)).fetchone()
        if not row:
            return
        old = row["workflow_status"] or "discovered"
        if old == target:
            return
        c.execute("update opportunities set workflow_status=? where dedupe_key=?", (target, dedupe_key))
        c.execute(
            "insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)",
            (
                dedupe_key,
                "status_transition",
                now(),
                "wfg_approval_dispatcher",
                json.dumps({"from": old, "to": target, "reason": f"{approval.get('gate')} approved; downstream task queued", "approval_id": approval.get("approval_id")}, sort_keys=True),
            ),
        )
        c.commit()


def insert_dispatch(approval: dict[str, Any], downstream: dict[str, Any], task: dict[str, Any], dispatch_output: str) -> None:
    with con() as c:
        c.execute(
            """insert or ignore into approval_dispatches
               (approval_id,dedupe_key,gate,decision,dispatch_type,status,task_id,task_title,task_body,created_at,dispatched_at,dispatch_output)
               values(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval["approval_id"],
                approval["dedupe_key"],
                approval.get("gate") or "",
                approval.get("decision") or "",
                downstream["dispatch_type"],
                "queued",
                task.get("task_id"),
                task.get("title"),
                task.get("body"),
                now(),
                now(),
                dispatch_output,
            ),
        )
        c.commit()


def post_operational_update(approval: dict[str, Any], downstream: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    route = load_route(downstream["route"])
    if not route:
        return None
    opp = opportunity_for_key(approval["dedupe_key"])
    text = (
        f"✅ Approval accepted — next WFG task queued\n\n"
        f"Opportunity: {opp.get('title') or approval['dedupe_key']}\n"
        f"Gate approved: {approval.get('gate')}\n"
        f"Next task: {downstream['title_prefix']}\n"
        f"Kanban task: {task.get('task_id') or 'created'}\n"
        f"Next approval gate expected: {downstream['next_gate']}\n\n"
        f"No external outreach, pricing approval, or submission has happened yet."
    )
    result = send_telegram(route["target"], text)
    event(approval["dedupe_key"], "approval_operational_followup", {"route": route.get("label"), "target": route.get("target"), "telegram_ok": bool(result.get("ok")), "message_id": (result.get("result") or {}).get("message_id"), "task_id": task.get("task_id")})
    return result


def create_workflow_task(approval: dict[str, Any], downstream: dict[str, Any]) -> int:
    """DB-first task queue write (consensus plan Section 5: workflow_tasks is
    the source of truth; Kanban is a tolerant mirror). Idempotent by key;
    returns the task_id either way."""
    idem = f"wfg:{approval['approval_id']}:{downstream['dispatch_type']}"
    opp = opportunity_for_key(approval["dedupe_key"])
    with con() as c:
        c.execute(
            """insert or ignore into workflow_tasks
               (dedupe_key,opportunity_folder,role_id,task_type,current_state,input_json,idempotency_key,created_at,next_gate,environment)
               values(?,?,?,?,?,?,?,?,?,?)""",
            (
                approval["dedupe_key"],
                folder_for_approval(approval.get("record_path") or ""),
                downstream["route"],
                downstream["dispatch_type"],
                "queued",
                json.dumps({"approval_id": approval["approval_id"], "gate_id": downstream.get("gate_id"), "record_path": approval.get("record_path"), "title": opp.get("title")}, sort_keys=True),
                idem,
                now(),
                downstream["next_gate"],
                approval.get("environment") or "production",
            ),
        )
        row = c.execute("select task_id from workflow_tasks where idempotency_key=?", (idem,)).fetchone()
        c.commit()
    return int(row["task_id"])


def mirror_to_kanban(approval: dict[str, Any], downstream: dict[str, Any], wf_task_id: int, do_dispatch: bool) -> tuple[dict[str, Any], str, str]:
    """Attempt the Kanban mirror. Never raises: mirror failure must not lose
    the workflow_tasks row. Returns (task_info, dispatch_output, error)."""
    try:
        task = create_kanban_task(approval, downstream)
        dispatch_output = dispatch_kanban() if do_dispatch else ""
        with con() as c:
            c.execute(
                "update workflow_tasks set kanban_task_id=?, kanban_mirror_status='mirrored' where task_id=?",
                (str(task.get("task_id") or ""), wf_task_id),
            )
            c.commit()
        return task, dispatch_output, ""
    except Exception as exc:
        with con() as c:
            c.execute(
                "update workflow_tasks set kanban_mirror_status='failed', error=? where task_id=?",
                (str(exc)[:2000], wf_task_id),
            )
            c.commit()
        event(approval["dedupe_key"], "kanban_mirror_failed", {"approval_id": approval["approval_id"], "workflow_task_id": wf_task_id, "error": str(exc)[:500]})
        return {"task_id": None, "title": f"{downstream['title_prefix']} (kanban mirror pending)"}, "", str(exc)


def record_refusal(approval: dict[str, Any], refusal: dict[str, Any]) -> bool:
    """Record a dispatch refusal exactly once per approval. Returns True the
    first time (so callers can alert once, not every pump run)."""
    with con() as c:
        cur = c.execute(
            """insert or ignore into approval_dispatches
               (approval_id,dedupe_key,gate,decision,dispatch_type,status,created_at,error)
               values(?,?,?,?,?,?,?,?)""",
            (approval["approval_id"], approval["dedupe_key"], approval.get("gate") or "", approval.get("decision") or "", "refused", "refused", now(), json.dumps(refusal, sort_keys=True)),
        )
        c.commit()
        first = bool(cur.rowcount)
    if first:
        event(approval["dedupe_key"], "approval_dispatch_refused", {"approval_id": approval["approval_id"], **refusal})
    return first


def retry_failed_mirror(approval: dict[str, Any], downstream: dict[str, Any], do_dispatch: bool) -> dict[str, Any] | None:
    idem = f"wfg:{approval['approval_id']}:{downstream['dispatch_type']}"
    with con() as c:
        row = c.execute("select task_id, kanban_mirror_status from workflow_tasks where idempotency_key=?", (idem,)).fetchone()
    if row and row["kanban_mirror_status"] == "failed":
        task, _, err = mirror_to_kanban(approval, downstream, int(row["task_id"]), do_dispatch)
        return {"retried_mirror": True, "mirror_ok": not err, "kanban_task_id": task.get("task_id")}
    return None


def run(button_id: str | None = None, approval_id: str | None = None, do_dispatch: bool = True) -> dict[str, Any]:
    migrate()
    approvals = approvals_to_dispatch(button_id=button_id, approval_id=approval_id)
    results = []
    created_any = False
    for approval in approvals:
        downstream, refusal = downstream_for_approval(approval)
        if refusal is not None:
            first = record_refusal(approval, refusal)
            results.append({"approval_id": approval["approval_id"], "dedupe_key": approval["dedupe_key"], "status": "refused", "first_refusal": first, **refusal})
            continue
        if downstream is None:
            continue
        prior = already_dispatched(approval["approval_id"], downstream["dispatch_type"])
        if prior:
            set_workflow_status_after_queue(approval, downstream)
            entry = {"approval_id": approval["approval_id"], "dedupe_key": approval["dedupe_key"], "dispatch_type": downstream["dispatch_type"], "status": "already_dispatched", "task_id": prior.get("task_id")}
            retried = retry_failed_mirror(approval, downstream, do_dispatch)
            if retried:
                entry.update(retried)
            results.append(entry)
            continue
        # DB task row first — the source of truth survives any mirror failure.
        wf_task_id = create_workflow_task(approval, downstream)
        created_any = True
        task, dispatch_output, mirror_error = mirror_to_kanban(approval, downstream, wf_task_id, do_dispatch)
        insert_dispatch(approval, downstream, {**task, "task_id": task.get("task_id") or f"wf:{wf_task_id}"}, dispatch_output)
        if mirror_error:
            with con() as c:
                c.execute("update approval_dispatches set error=? where approval_id=? and dispatch_type=?", (mirror_error[:2000], approval["approval_id"], downstream["dispatch_type"]))
                c.commit()
        set_workflow_status_after_queue(approval, downstream)
        event(approval["dedupe_key"], "approval_dispatch_queued", {"approval_id": approval["approval_id"], "dispatch_type": downstream["dispatch_type"], "workflow_task_id": wf_task_id, "kanban_task_id": task.get("task_id"), "kanban_mirror_ok": not mirror_error})
        followup = post_operational_update(approval, downstream, task)
        results.append({"approval_id": approval["approval_id"], "dedupe_key": approval["dedupe_key"], "dispatch_type": downstream["dispatch_type"], "status": "queued", "workflow_task_id": wf_task_id, "kanban_task_id": task.get("task_id"), "kanban_mirror_ok": not mirror_error, "followup_ok": None if followup is None else bool(followup.get("ok"))})
    return {"ok": True, "created_any": created_any, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--button-id")
    ap.add_argument("--approval-id")
    ap.add_argument("--no-kanban-dispatch", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(button_id=args.button_id, approval_id=args.approval_id, do_dispatch=not args.no_kanban_dispatch), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
