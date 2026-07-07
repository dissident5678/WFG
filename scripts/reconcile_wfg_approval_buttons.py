#!/usr/bin/env python3
"""Reconcile WFG Telegram approval-button decisions into workflow DB/files.

The Telegram callback handler records button decisions in approvals/button-registry.json
and decision-log.md. This script makes those decisions visible to the WFG workflow DB
used by intake/approval automation, and moves central pending packets to approved/closed.

Decision vocabulary (consensus plan Section 7): approved, denied, revise_requested,
held. Legacy registry statuses ('rejected', 'revise', 'hold') are normalized.
Revise/held packets stay in pending/ — they are still the active packet awaiting a
superseding version or release.

It is idempotent and safe to run frequently. It prints one line per reconciliation
action; no output means nothing changed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
DB = Path(os.environ.get('WFG_DB_PATH', str(ROOT / 'state' / 'wfg_workflow.sqlite3')))
REGISTRY = ROOT / 'approvals' / 'button-registry.json'
PENDING = ROOT / 'approvals' / 'pending'
APPROVED = ROOT / 'approvals' / 'approved'
CLOSED = ROOT / 'approvals' / 'closed'

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_gates  # noqa: E402

# Registry statuses normalized to the plan's decision vocabulary.
STATUS_MAP = {
    'approved': 'approved',
    'denied': 'denied',
    'rejected': 'denied',
    'revise': 'revise_requested',
    'revised': 'revise_requested',
    'revise_requested': 'revise_requested',
    'hold': 'held',
    'held': 'held',
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def read_registry() -> dict:
    if not REGISTRY.exists():
        return {}
    try:
        data = json.loads(REGISTRY.read_text(errors='ignore') or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def field(text: str, label: str) -> str:
    m = re.search(rf'^{re.escape(label)}:\s*(.+)$', text, re.M)
    return m.group(1).strip().strip('`') if m else ''


def parse_packet(path: Path) -> dict[str, str]:
    text = path.read_text(errors='ignore')
    return {
        'approval_id': field(text, 'Approval ID'),
        'gate_id': field(text, 'Gate ID'),
        'notice_id': field(text, 'Notice ID'),
        'solicitation': field(text, 'Solicitation number'),
        'artifact_version': field(text, 'Artifact/package version'),
        'artifact_hash': field(text, 'Artifact hash'),
        'approval_type': field(text, 'Approval type'),
        'title': field(text, 'Opportunity / project') or path.stem,
    }


def move_packet(packet: Path, decision: str) -> None:
    # Only terminal decisions move the packet. revise_requested/held packets
    # stay in pending/ as the active record awaiting rework or release.
    if decision == 'approved':
        dest_dir = APPROVED
    elif decision == 'denied':
        dest_dir = CLOSED
    else:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    pending = PENDING / packet.name
    dest = dest_dir / packet.name
    source = pending if pending.exists() else packet
    if source.exists() and not dest.exists():
        shutil.copy2(source, dest)
    if pending.exists():
        pending.unlink()


def resolve_gate_id(info: dict[str, str], row_gate: str) -> str | None:
    return wfg_gates.resolve_gate_id({
        'gate_id': info.get('gate_id') or '',
        'gate': info.get('approval_type') or row_gate or '',
    })


def gate_transition(gate_id: str | None, decision: str) -> str | None:
    """Post-decision workflow status. Approved transitions mirror the
    dispatcher's status_after_queue (harmless duplicate — both check the
    current value first). A denied Gate 1 marks the opportunity passed."""
    if gate_id is None:
        return None
    if decision == 'denied':
        return 'passed' if gate_id == 'GATE_1_PURSUE' else None
    if decision != 'approved':
        return None
    entry = wfg_gates.GATES.get(gate_id) or {}
    dispatch = entry.get('dispatch') or {}
    return dispatch.get('status_after_queue')


def main() -> int:
    reg = read_registry()
    if not reg or not DB.exists():
        return 0
    changed: list[str] = []
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for button_id, rec in sorted(reg.items()):
        decision = STATUS_MAP.get((rec.get('status') or '').lower())
        if decision is None:
            continue
        packet = Path(rec.get('resolved_packet_path') or rec.get('packet_path') or '')
        if not packet.exists():
            continue
        info = parse_packet(packet)
        notice = info.get('notice_id')
        version = info.get('artifact_version')
        if not notice or not version:
            continue
        dedupe = 'notice:' + notice
        packet_approval_id = info.get('approval_id') or ''
        row = None
        if packet_approval_id:
            row = con.execute(
                """select id, decision, valid, gate, gate_id from approvals
                       where approval_id=? and valid=1
                       order by id desc limit 1""",
                (packet_approval_id,),
            ).fetchone()
        if row is None:
            row = con.execute(
                """select id, decision, valid, gate, gate_id from approvals
                       where dedupe_key=? and artifact_version=? and valid=1
                       order by id desc limit 1""",
                (dedupe, version),
            ).fetchone()
        if not row or row['decision'] == decision:
            move_packet(packet, decision)
            continue
        gate_id = resolve_gate_id(info, row['gate'] or '')
        decided_at = rec.get('decided_at') or now()
        approver = rec.get('decided_by') or 'Telegram approval button'
        approver_id = rec.get('decided_by_id')
        con.execute(
            """update approvals
                  set decision=?, decided_at=?, approver=?, telegram_user_id=?, used_at=?, conditions=?, gate_id=coalesce(?, gate_id)
                where id=?""",
            (decision, decided_at, approver, approver_id,
             decided_at if decision == 'approved' else None,
             f'reconciled from approval button {button_id}', gate_id, row['id']),
        )
        new_status = gate_transition(gate_id, decision)
        if new_status:
            current = con.execute('select workflow_status from opportunities where dedupe_key=?', (dedupe,)).fetchone()
            current_status = current['workflow_status'] if current else None
            # Do not regress active work back to an earlier gate.
            safe = True
            if new_status == 'pursuing' and current_status not in {None, 'awaiting_pursue_decision', 'gate1_pending_pursue', 'pursuing'}:
                safe = False
            if safe:
                con.execute('update opportunities set workflow_status=? where dedupe_key=?', (new_status, dedupe))
        con.execute(
            'insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)',
            (dedupe, 'approval_button_reconciled', now(), 'approval_reconciler', json.dumps({
                'button_id': button_id,
                'decision': decision,
                'gate_id': gate_id,
                'packet': str(packet),
                'artifact_version': version,
            }, sort_keys=True)),
        )
        move_packet(packet, decision)
        changed.append(f'{decision} {notice} {version[:12]} via {button_id}')
    con.commit()
    for line in changed:
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
