#!/usr/bin/env python3
"""Research-first preflight gate for WFG subcontractor bid packets.

Rule (consensus plan): Research first. Packet second. Outreach third. Approval
before external action always. A subcontractor bid packet is not a research
tool — it is the output of completed research. This script decides whether the
research is actually complete.

How it decides: it runs the packet builder's OWN extraction
(wfg_sub_bid_packet.build_packet_data) in dry-run and fails when the extraction
falls back to any placeholder output ("See solicitation package",
"To be set by WFG before outreach", "Agency not listed...", generic scope). So
PASS is a hard guarantee: the packet will render with real values, because the
same code that renders it just did.

Outputs in the opportunity folder:
- research_preflight.json  — status PASS/FAIL, per-check results, and sha256
  hashes of the research artifacts. Consumers must verify these hashes: if an
  artifact changed after PASS, the preflight is stale and must be re-run.
- research_blocker.md      — written on FAIL (and removed on PASS): explicit,
  numbered instructions telling the researcher exactly which fact is missing,
  where to look for it, and the exact line format to write.

Usage:
    python3 scripts/wfg_research_preflight.py <opportunity_folder> [--queue-next]

--queue-next: on PASS, insert one idempotent workflow_tasks row
(gate1_packet_outreach_prep) so the packet/outreach phase is queued as its own
tracked task.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wfg_sub_bid_packet as builder  # noqa: E402

PREFLIGHT_VERSION = 1
MARKER_NAME = "research_preflight.json"
BLOCKER_NAME = "research_blocker.md"

# Scaffold markers written by the intake drafts (wfg_phase2.py). They are the
# signature of unfinished research. They are allowed in 04_MISSING_INFORMATION.md
# (that file is where uncertainty belongs) and forbidden everywhere else.
SCAFFOLD_MARKERS = (
    "[USER INPUT REQUIRED]",
    "[DOCUMENT MISSING]",
    "[ASSUMPTION — MUST BE CONFIRMED]",
    "[ASSUMPTION - MUST BE CONFIRMED]",
    "[SUBCONTRACTOR NOT VERIFIED]",
    "[PRICE NOT APPROVED]",
    "[LEGAL OR COMPLIANCE REVIEW REQUIRED]",
    "[QUOTE NOT RECEIVED]",
    "[NOT READY FOR SUBMISSION]",
    "[DOCUMENT MISSING/SEE SAM LOCATION]",
)

# Exact fallback outputs of build_packet_data when a fact was not found.
# Preflight fails on any of these appearing in a required field.
FALLBACK_VALUES = {
    "agency_name": ("Agency not listed",),
    "project_location": ("See solicitation package",),
    "government_due_datetime": ("See solicitation package",),
    "sub_quote_due_datetime": ("To be set by WFG before outreach",),
}
GENERIC_SCOPE = "Price the trade scope described in the solicitation documents and attachments."

# The five research artifacts the packet builder consumes (canonical names
# first; load_artifacts also accepts the alternates).
REQUIRED_ARTIFACTS = {
    "brief": "02_SOLICITATION_BRIEF.md",
    "scope": "05_SCOPE_DECOMPOSITION.md",
    "criteria": "06_SUBCONTRACTOR_SOURCING_CRITERIA.md",
    "missing": "04_MISSING_INFORMATION.md",
    "manifest": "attachment_manifest.md",
}
# Uncertainty belongs in 04_MISSING_INFORMATION.md; scaffold markers there are fine.
MARKERS_ALLOWED_IN = {"missing"}

# What to write in 02_SOLICITATION_BRIEF.md, in exactly this line format, so the
# packet builder's extraction finds it. This block is reused by the blocker file
# and by the dispatcher task instructions — single source of truth.
BRIEF_LINE_FORMAT = """\
- Title: <exact project title from the solicitation>
- Agency: <buyer agency and office, from the solicitation or SAM.gov listing>
- Solicitation: <solicitation/RFQ number>
- Notice ID: <SAM.gov notice id>
- Place of performance: <city, state, and site/base name>
- Response deadline: <YYYY-MM-DD HH:MM with timezone — the government due date>
- Questions due: <date/time, or the word None if the solicitation sets none>
- Site visit: <date/time and location, or the word None>
- POP: <period of performance, e.g. "12 months from NTP">
- Pricing format: <how the government wants pricing: CLIN table / lump sum / unit prices>

## Scope summary
- <one bullet per real work item, taken from the SOW/PWS — never invented>

## Price sheet
- <one bullet per CLIN/line item if the solicitation has a price schedule; omit this section only if none exists>"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Check:
    def __init__(self, check_id: str, ok: bool, detail: str, *, required: bool = True,
                 fix: str = "") -> None:
        self.check_id = check_id
        self.ok = ok
        self.detail = detail
        self.required = required
        self.fix = fix

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check_id, "ok": self.ok, "required": self.required,
                "detail": self.detail, "fix": self.fix}


def scaffold_hits(text: str) -> list[str]:
    return [m for m in SCAFFOLD_MARKERS if m in text]


def run_checks(opp: Path) -> tuple[list[Check], dict[str, str]]:
    checks: list[Check] = []
    artifacts = builder.load_artifacts(opp)
    hashes: dict[str, str] = {}

    # Layer 1 — the five research artifacts exist and are not scaffolds.
    for key, canonical in REQUIRED_ARTIFACTS.items():
        path, text = artifacts.get(key, (None, ""))
        if path is None:
            checks.append(Check(
                f"artifact_exists:{canonical}", False,
                f"{canonical} does not exist in the opportunity folder.",
                fix=f"Create {canonical} from the actual solicitation documents in source/ and extracted-text/.",
            ))
            continue
        hashes[canonical] = sha256_file(path)
        checks.append(Check(f"artifact_exists:{canonical}", True, f"found {path.name}"))
        hits = scaffold_hits(text)
        if hits and key not in MARKERS_ALLOWED_IN:
            checks.append(Check(
                f"artifact_no_scaffold:{canonical}", False,
                f"{canonical} still contains unfinished scaffold markers: {', '.join(hits)}.",
                fix=(f"Replace every scaffold marker in {canonical} with a real value read from the source documents. "
                     "If a fact truly cannot be found, move it to 04_MISSING_INFORMATION.md with a note saying which "
                     "documents were checked — never leave the marker in this file."),
            ))
        elif key not in MARKERS_ALLOWED_IN:
            checks.append(Check(f"artifact_no_scaffold:{canonical}", True, "no scaffold markers"))

    # Layer 2 — dry-run the packet builder's extraction and reject fallbacks.
    data, _review, _ = builder.build_packet_data(opp)

    title = data.get("project_title") or ""
    checks.append(Check(
        "fact:project_title", bool(title) and title != opp.name,
        f"project_title={title!r}",
        fix="Add a `- Title:` line to 02_SOLICITATION_BRIEF.md with the exact project title from the solicitation.",
    ))
    for field, fallbacks in FALLBACK_VALUES.items():
        value = str(data.get(field) or "")
        bad = (not value) or any(value.startswith(f) for f in fallbacks)
        label = {
            "agency_name": "- Agency:",
            "project_location": "- Place of performance:",
            "government_due_datetime": "- Response deadline: (must be a parseable date/time)",
            "sub_quote_due_datetime": "- Response deadline: (the sub quote due is derived from it)",
        }[field]
        checks.append(Check(
            f"fact:{field}", not bad, f"{field}={value!r}",
            fix=f"Add/complete the `{label}` line in 02_SOLICITATION_BRIEF.md using the source documents.",
        ))
    sol = str(data.get("solicitation_number") or "")
    notice = str(data.get("notice_id") or "")
    has_id = (sol and sol != "Not listed") or (notice and notice != "Not listed")
    checks.append(Check(
        "fact:solicitation_or_notice_id", has_id,
        f"solicitation_number={sol!r}, notice_id={notice!r}",
        fix="Add `- Solicitation:` and/or `- Notice ID:` lines to 02_SOLICITATION_BRIEF.md.",
    ))
    scope_items = data.get("scope_items") or []
    scope_real = bool(scope_items) and not any(
        GENERIC_SCOPE in str(x.get("scope_description", "")) for x in scope_items
    )
    checks.append(Check(
        "fact:real_scope_items", scope_real,
        f"{len(scope_items)} scope item(s); generic fallback present: {not scope_real}",
        fix=("Fill `## Work packages` in 05_SCOPE_DECOMPOSITION.md (or `## Scope summary` in the brief) with one "
             "bullet per real work item taken from the SOW/PWS. Do not write a generic sentence."),
    ))
    trade = str(data.get("trades_requested") or "")
    checks.append(Check(
        "fact:trade_identified", trade != "generic trade-specific fit",
        f"trade rule matched: {trade!r}", required=False,
        fix="If the trade is genuinely specialty, ignore this warning; otherwise name the trade clearly in 06_SUBCONTRACTOR_SOURCING_CRITERIA.md and the brief.",
    ))

    # Layer 3 — source documents present and parsed.
    source_dir = opp / "source"
    source_files = [p for p in source_dir.glob("*") if p.is_file()] if source_dir.exists() else []
    manifest_text = artifacts.get("manifest", (None, ""))[1]
    if source_files:
        checks.append(Check("sources:present", True, f"{len(source_files)} file(s) in source/"))
    else:
        checks.append(Check(
            "sources:present", False,
            "source/ is empty or missing — no solicitation documents have been downloaded.",
            fix="Download the SAM.gov listing export and every solicitation attachment into source/ before writing any artifact.",
        ))
    extract_dir = opp / "extracted-text"
    unparsed = [p.name for p in source_files if p.suffix.lower() == ".pdf"
                and not (extract_dir / f"{p.name}.extracted.txt").exists()]
    checks.append(Check(
        "sources:pdfs_extracted", not unparsed,
        ("all source PDFs have extracted text" if not unparsed
         else f"unparsed PDFs (no extracted-text/*.extracted.txt): {', '.join(unparsed)}"),
        required=False,
        fix="Extract text from each listed PDF into extracted-text/ (or record in attachment_manifest.md why it is image-only and what a human must read).",
    ))
    manifest_has_rows = any(line.strip().startswith(("-", "|", "*")) for line in manifest_text.splitlines())
    checks.append(Check(
        "sources:manifest_lists_attachments", manifest_has_rows,
        "attachment_manifest.md lists attachments" if manifest_has_rows
        else "attachment_manifest.md has no attachment entries.",
        fix="List every file in source/ in attachment_manifest.md with one line each: name, what it is, and whether a subcontractor needs it to price.",
    ))
    return checks, hashes


def write_blocker(opp: Path, failed: list[Check], warnings: list[Check]) -> Path:
    lines = [
        "# RESEARCH BLOCKER — do not build a subcontractor packet yet",
        "",
        f"Generated by scripts/wfg_research_preflight.py at {now()}.",
        "This opportunity FAILED the research preflight. A placeholder packet is worse",
        "than no packet. Complete the numbered steps below, in order, then re-run:",
        "",
        f"    python3 scripts/wfg_research_preflight.py \"{opp}\"",
        "",
        "## What is missing (fix each, in order)",
        "",
    ]
    for i, c in enumerate(failed, 1):
        lines.append(f"{i}. **{c.check_id}** — {c.detail}")
        if c.fix:
            lines.append(f"   - How to fix: {c.fix}")
    if warnings:
        lines += ["", "## Warnings (report, do not ignore silently)", ""]
        for c in warnings:
            lines.append(f"- {c.check_id} — {c.detail}")
    lines += [
        "",
        "## Where to look",
        "",
        "1. `source/` — the downloaded SAM.gov export and solicitation attachments. If empty, download them first.",
        "2. `extracted-text/` — machine-readable text of each attachment. Extract before reading.",
        "3. Read, in this order: amendments and Q&A, then the solicitation/RFQ and SOW/PWS, then price",
        "   sheets/CLINs, then wage determinations, then site visit notices. SAM.gov metadata is a last resort.",
        "",
        "## Exact format 02_SOLICITATION_BRIEF.md must use",
        "",
        "The packet builder reads these exact labels. Write them exactly like this:",
        "",
        "```markdown",
        BRIEF_LINE_FORMAT,
        "```",
        "",
        "Every fact must come from a source document. Record the source file next to each",
        "fact or in the source map. If a fact cannot be found anywhere, put it in",
        "04_MISSING_INFORMATION.md with the list of documents you checked — never invent it,",
        "never leave a scaffold marker in the brief.",
    ]
    blocker = opp / BLOCKER_NAME
    blocker.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return blocker


def preflight_status(opp: Path) -> dict[str, Any]:
    """For consumers (packet builder, outreach cycle): is there a current PASS?

    Returns {"ok": bool, "reason": str}. ok is True only when the marker exists,
    says PASS, and every hashed artifact is unchanged since the check ran.
    """
    marker = opp / MARKER_NAME
    if not marker.exists():
        return {"ok": False, "reason": f"{MARKER_NAME} not found — the research preflight has never run for this opportunity."}
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "reason": f"{MARKER_NAME} is unreadable — re-run the preflight."}
    if data.get("status") != "PASS":
        return {"ok": False, "reason": f"research preflight status is {data.get('status')!r} — research is incomplete. Read {BLOCKER_NAME}."}
    for name, digest in (data.get("artifact_hashes") or {}).items():
        p = opp / name
        if not p.exists() or sha256_file(p) != digest:
            return {"ok": False, "reason": f"{name} changed after the preflight passed — the PASS is stale. Re-run the preflight."}
    return {"ok": True, "reason": "preflight PASS and artifacts unchanged"}


def queue_next_task(opp: Path, artifact_hashes: dict[str, str]) -> dict[str, Any]:
    import wfg_tracking_schema
    digest = hashlib.sha256(json.dumps(artifact_hashes, sort_keys=True).encode()).hexdigest()[:12]
    idem = f"preflight:g1b:{opp.name}:{digest}"
    c = wfg_tracking_schema.con()
    try:
        wfg_tracking_schema.ensure_phase2_workflow_schema(c)
        dedupe = wfg_tracking_schema.opportunity_dedupe_for_folder(c, str(opp))
        cur = c.execute(
            """insert or ignore into workflow_tasks
               (dedupe_key,opportunity_folder,role_id,task_type,current_state,input_json,idempotency_key,created_at,next_gate)
               values(?,?,?,?,?,?,?,?,?)""",
            (dedupe, str(opp), "outreach", "gate1_packet_outreach_prep", "queued",
             json.dumps({"reason": "research preflight PASS", "artifact_digest": digest}),
             idem, now(), "GATE_2_PACKAGE"),
        )
        c.commit()
        return {"queued": bool(cur.rowcount), "idempotency_key": idem}
    finally:
        c.close()


def run(opp: Path, queue_next: bool = False) -> dict[str, Any]:
    checks, hashes = run_checks(opp)
    failed = [c for c in checks if c.required and not c.ok]
    warnings = [c for c in checks if not c.required and not c.ok]
    status = "PASS" if not failed else "FAIL"
    report = {
        "preflight_version": PREFLIGHT_VERSION,
        "status": status,
        "checked_at": now(),
        "opportunity_folder": str(opp),
        "artifact_hashes": hashes,
        "checks": [c.as_dict() for c in checks],
        "failed": [c.check_id for c in failed],
        "warnings": [c.check_id for c in warnings],
    }
    (opp / MARKER_NAME).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    blocker = opp / BLOCKER_NAME
    if status == "FAIL":
        report["blocker"] = str(write_blocker(opp, failed, warnings))
    elif blocker.exists():
        blocker.unlink()
    if status == "PASS" and queue_next:
        report["next_task"] = queue_next_task(opp, hashes)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("opportunity_folder", type=Path)
    ap.add_argument("--queue-next", action="store_true",
                    help="On PASS, queue the gate1_packet_outreach_prep workflow task (idempotent).")
    args = ap.parse_args()
    opp = args.opportunity_folder.resolve()
    if not opp.is_dir():
        print(json.dumps({"status": "ERROR", "error": f"opportunity folder not found: {opp}"}))
        return 2
    report = run(opp, queue_next=args.queue_next)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
