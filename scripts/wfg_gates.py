#!/usr/bin/env python3
"""WFG approval gate registry.

Single source of truth for machine-readable gate IDs per the consensus plan
(docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md, Sections 5 and 7).

The dispatcher must match on gate_id only. Gate-name substring matching is
forbidden: under the old code, any approval whose title contained "Outreach"
dispatched the send path. Legacy approvals map to gate IDs only through the
exact-text table below; anything unmapped is refused, never guessed.
"""
from __future__ import annotations

import re
from typing import Any

DECISIONS = ("approved", "denied", "revise_requested", "held")

# Gates whose approval queues a follow-on internal task carry a "dispatch"
# entry. Gates with dispatch=None never dispatch anything from the approval
# alone; no_dispatch_reason says why.
GATES: dict[str, dict[str, Any]] = {
    "GATE_1_PURSUE": {
        "name": "Gate 1 — Approve Opportunity Pursuit",
        "dispatch": {
            "dispatch_type": "gate1_subcontractor_sourcing",
            "route": "outreach",
            "status_after_queue": "pursuing",
            "title_prefix": "Gate 1 approved — start subcontractor sourcing",
            "next_gate_id": "GATE_2_PACKAGE",
        },
    },
    "GATE_2_PACKAGE": {
        "name": "Gate 2 — Approve Outreach Package (packet + recipients + message)",
        "dispatch": {
            "dispatch_type": "gate2_send_approval_prep",
            "route": "outreach",
            "status_after_queue": "gate2_pending_outreach_send",
            "title_prefix": "Gate 2 package approved — prepare GATE_2_SEND approval",
            "next_gate_id": "GATE_2_SEND",
        },
    },
    "GATE_2_SEND": {
        "name": "Gate 2-SEND — Approve Sending Outreach",
        "dispatch": {
            "dispatch_type": "gate2_outreach_execution",
            "route": "outreach",
            "status_after_queue": "outreach_approved",
            "title_prefix": "Gate 2-SEND approved — execute approved outreach package",
            "next_gate_id": "GATE_3_STRATEGY",
        },
    },
    "GATE_3_FOLLOWUP": {
        "name": "Gate 3A — Approve Quote Follow-Up",
        "dispatch": {
            "dispatch_type": "gate3_followup_execution",
            "route": "outreach",
            "status_after_queue": None,
            "title_prefix": "Gate 3A approved — execute approved follow-up",
            "next_gate_id": "GATE_3_STRATEGY",
        },
    },
    "GATE_3_STRATEGY": {
        "name": "Gate 3B — Approve Bid Strategy",
        "dispatch": {
            "dispatch_type": "gate3_proposal_package",
            "route": "proposal",
            "status_after_queue": "proposal_in_progress",
            "title_prefix": "Gate 3B approved — assemble proposal package",
            "next_gate_id": "GATE_4_PACKAGE",
        },
    },
    "GATE_4_PACKAGE": {
        "name": "Gate 4 — Approve Final Proposal Package",
        "dispatch": {
            "dispatch_type": "gate4_human_submission_prep",
            "route": "submission",
            "status_after_queue": "gate5_pending_submission",
            "title_prefix": "Gate 4 approved — prepare human submission handoff",
            "next_gate_id": "GATE_5_SUBMIT",
        },
    },
    "GATE_5_SUBMIT": {
        "name": "Gate 5 — Approve Submission (human submits)",
        "elevated_confirmation": True,  # exact-text reply with package hash prefix; not one-tap
        "dispatch": {
            "dispatch_type": "gate5_submission_proof_tracking",
            "route": "submission",
            "status_after_queue": "awaiting_human_submission",
            "title_prefix": "Gate 5 approved — track human submission and archive proof",
            "next_gate_id": "GATE_6_CLOSE",
        },
    },
    "GATE_6_CLOSE": {
        "name": "Gate 6 — Approve Archive/Closeout",
        "dispatch": {
            "dispatch_type": "gate6_closeout",
            "route": "system_ops",
            "status_after_queue": "closed_archived",
            "title_prefix": "Gate 6 approved — archive and closeout",
            "next_gate_id": None,
        },
    },
    "GATE_AMEND_CONTINUE": {
        "name": "Gate A — Approve Continuing Under Amendment",
        "dispatch": {
            "dispatch_type": "amend_resume",
            "route": "compliance",
            "status_after_queue": None,  # resume stage is chosen by Nick in the approval
            "title_prefix": "Amendment reviewed — resume from approved stage",
            "next_gate_id": None,
        },
    },
    # Legacy broad Gate 2 approvals authorized packet+list+draft+send in one
    # gate under the old model. Consensus plan Section 5: never execute one
    # automatically; the migration queues a task to create a new
    # GATE_2_PACKAGE / GATE_2_SEND cycle instead.
    "LEGACY_GATE_2_BROAD": {
        "name": "LEGACY Gate 2 — Authorize External Outreach (superseded model)",
        "dispatch": None,
        "no_dispatch_reason": "legacy broad Gate 2 approval; requires a new GATE_2_PACKAGE/GATE_2_SEND cycle (plan Section 5)",
    },
}

# Exact-text map for gate strings that exist in historical approval rows.
# Exact match after whitespace normalization — never substring matching.
LEGACY_GATE_TEXT_MAP: dict[str, str] = {
    "GATE 1 — Pursue or Pass": "GATE_1_PURSUE",
    "GATE 2 — Authorize External Outreach": "LEGACY_GATE_2_BROAD",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


_LEGACY_NORMALIZED = {_norm(k): v for k, v in LEGACY_GATE_TEXT_MAP.items()}


def resolve_gate_id(approval: dict[str, Any]) -> str | None:
    """Resolve an approval row to a known gate_id, or None (caller must refuse)."""
    gid = _norm(approval.get("gate_id") or "")
    if gid in GATES:
        return gid
    return _LEGACY_NORMALIZED.get(_norm(approval.get("gate") or ""))


def gate_display_name(gate_id: str | None) -> str:
    if gate_id and gate_id in GATES:
        return GATES[gate_id]["name"]
    return gate_id or "unknown gate"
