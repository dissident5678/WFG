#!/usr/bin/env python3
"""Sync WFG workflow data into a local Obsidian vault.

The vault is a human-readable wiki generated from the canonical WFG SQLite DB
and opportunity artifacts. Generated sections are bounded so manual notes can be
kept safely outside generated blocks.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", PROJECT / "state" / "wfg_workflow.sqlite3")).resolve()
VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", PROJECT / "obsidian-vault")).resolve()
GENERATED_START = "<!-- WFG-GENERATED:START -->"
GENERATED_END = "<!-- WFG-GENERATED:END -->"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def slugify(value: str, max_len: int = 90) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")[:max_len] or "unknown"


def yaml_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\n", " ").strip()
    if not s:
        return ""
    if re.search(r"[:#\[\]{},&*?!|>'\"%@`]|^[-]", s):
        return json.dumps(s)
    return s


def frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{k}: {yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def replace_generated(existing: str, generated: str) -> str:
    block = f"{GENERATED_START}\n{generated.rstrip()}\n{GENERATED_END}\n"
    if GENERATED_START in existing and GENERATED_END in existing:
        return re.sub(re.escape(GENERATED_START) + r"[\s\S]*?" + re.escape(GENERATED_END) + r"\n?", block, existing)
    return existing.rstrip() + "\n\n" + block


def write_generated_note(path: Path, fm: dict[str, Any], title: str, generated_body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    manual_notes = ""
    if existing:
        before_generated = existing.split(GENERATED_START, 1)[0]
        marker = "## Manual Notes"
        if marker in before_generated:
            manual_notes = before_generated.split(marker, 1)[1].strip()
    base = frontmatter(fm) + f"# {title}\n\n## Manual Notes\n\n{manual_notes}\n\n"
    content = replace_generated(base, generated_body)
    path.write_text(content, encoding="utf-8")


def is_generated_note(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return GENERATED_START in text and GENERATED_END in text


def archive_stale_generated_notes(expected_paths: list[Path]) -> list[str]:
    """Move generated WFG notes no longer produced by sync to an archive folder.

    This keeps manual notes recoverable while preventing old fallback filenames,
    test fixtures, and pre-stable contact filenames from appearing in Dataview.
    """
    expected = {p.resolve() for p in expected_paths}
    managed_dirs = [
        VAULT / "01-Opportunities",
        VAULT / "03-Companies",
        VAULT / "04-Contacts",
        VAULT / "05-Emails",
        VAULT / "11-Approvals",
    ]
    stale = []
    for root in managed_dirs:
        if not root.exists():
            continue
        for path in root.glob("**/*.md"):
            if path.resolve() not in expected and is_generated_note(path):
                stale.append(path)
    if not stale:
        return []
    archive_root = PROJECT / "backups" / f"obsidian-archived-generated-duplicates-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    archived = []
    for path in stale:
        rel = path.relative_to(VAULT)
        dest = archive_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        archived.append(str(rel))
    return archived


def ensure_dirs() -> None:
    for rel in [
        "00-Dashboards", "01-Opportunities", "02-Agencies", "03-Companies", "04-Contacts", "05-Emails",
        "06-Meetings-Calls", "07-Proposals", "08-Compliance", "09-Research", "10-Templates", "11-Approvals",
        "12-Tasks", "99-Attachments",
    ]:
        (VAULT / rel).mkdir(parents=True, exist_ok=True)


def write_templates() -> None:
    templates = {
        "Opportunity.md": """---
type: opportunity
dedupe_key:
opportunity_folder:
status:
due_date:
tags:
  - wfg/opportunity
---
# <% tp.file.title %>

## Snapshot

## Manual Notes

## Related
""",
        "Company.md": """---
type: company
subcontractor_id:
legal_name:
primary_email:
primary_phone:
tags:
  - wfg/company
  - wfg/subcontractor
---
# <% tp.file.title %>

## Profile

## Manual Notes
""",
        "Contact.md": """---
type: contact
contact_id:
subcontractor_id:
email:
phone:
tags:
  - wfg/contact
---
# <% tp.file.title %>

## Notes
""",
        "Email.md": """---
type: email
interaction_id:
direction:
status:
dedupe_key:
gmail_message_id:
gmail_thread_id:
tags:
  - wfg/email
---
# <% tp.file.title %>

## Summary
""",
        "Approval.md": """---
type: approval
approval_id:
dedupe_key:
status: pending
tags:
  - wfg/approval
---
# <% tp.file.title %>

## Decision
""",
    }
    for name, content in templates.items():
        p = VAULT / "10-Templates" / name
        if not p.exists():
            p.write_text(content, encoding="utf-8")


def opportunity_title_from_folder(folder: str, fallback: str = "Opportunity") -> str:
    if folder:
        name = Path(folder).name
        m = re.match(r"[0-9a-f]{32}-(.*)", name)
        if m:
            return m.group(1).replace("-", " ").title()
    return fallback or "Opportunity"


def load_opportunity_rows(c: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = []
    q = """
    select oi.dedupe_key, oi.opportunity_folder, o.title, o.notice_id, o.solicitation_number, o.agency, '' office,
           o.naics, '' psc, o.set_aside, o.response_deadline, o.notice_type archive_type, o.place_of_performance,
           o.sam_link ui_url, coalesce(o.workflow_status, oi.status) status
    from opportunity_intakes oi
    left join opportunities o on o.dedupe_key=oi.dedupe_key
    where coalesce(oi.environment, 'production') = 'production'
      and coalesce(o.environment, 'production') = 'production'
      and coalesce(o.is_test_fixture, 0) = 0
    order by oi.id desc
    limit 500
    """
    try:
        for r in c.execute(q):
            rows.append(dict(r))
    except sqlite3.OperationalError:
        # Fallback for older DB shapes.
        for r in c.execute("select dedupe_key, opportunity_folder from opportunity_intakes where coalesce(environment, 'production') = 'production' order by id desc limit 500"):
            d = dict(r); d.update({"title": opportunity_title_from_folder(d.get("opportunity_folder", ""))}); rows.append(d)
    deduped = []
    seen = set()
    for row in rows:
        key = row.get("dedupe_key") or ""
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def sync_opportunities(c: sqlite3.Connection) -> list[Path]:
    paths = []
    for r in load_opportunity_rows(c):
        title = r.get("title") or opportunity_title_from_folder(r.get("opportunity_folder", ""), r.get("dedupe_key", "Opportunity"))
        dedupe = r.get("dedupe_key") or ""
        p = VAULT / "01-Opportunities" / f"{slugify(dedupe.replace(':','-') + '-' + title)}.md"
        subs = [dict(x) for x in c.execute(
            """select s.id, s.legal_name, l.status from subcontractor_opportunity_links l
               join subcontractors s on s.id=l.subcontractor_id
               where l.dedupe_key=? and coalesce(s.source, '') != 'manual-test'
               order by s.legal_name""",
            (dedupe,),
        )]
        interactions = [dict(x) for x in c.execute(
            """select i.id, s.legal_name, i.direction, i.status, i.interaction_type, i.occurred_at, i.subject
               from subcontractor_interactions i join subcontractors s on s.id=i.subcontractor_id
               where i.dedupe_key=? and coalesce(s.source, '') != 'manual-test'
               order by datetime(coalesce(i.occurred_at,'1970-01-01')) desc limit 30""",
            (dedupe,),
        )]
        generated = [
            "## Snapshot",
            f"- Dedupe key: `{dedupe}`",
            f"- Folder: `{r.get('opportunity_folder') or ''}`",
            f"- Agency: {r.get('agency') or 'UNKNOWN'}",
            f"- Solicitation: {r.get('solicitation_number') or 'UNKNOWN'}",
            f"- Due date: {r.get('response_deadline') or 'UNKNOWN'}",
            f"- Status: {r.get('status') or 'UNKNOWN'}",
            "",
            "## Subcontractors",
        ]
        if subs:
            for s in subs:
                generated.append(f"- [[{s['legal_name']}]] — {s.get('status') or 'UNKNOWN'} (ID {s['id']})")
        else:
            generated.append("- None linked yet.")
        generated += ["", "## Recent Communications"]
        if interactions:
            for i in interactions:
                generated.append(f"- {i.get('occurred_at') or ''} — {i['legal_name']} — {i['direction']} {i['interaction_type']} / {i['status']}: {i.get('subject') or ''}")
        else:
            generated.append("- None recorded yet.")
        write_generated_note(p, {
            "type": "opportunity", "dedupe_key": dedupe, "opportunity_folder": r.get("opportunity_folder") or "",
            "notice_id": r.get("notice_id") or "", "solicitation_number": r.get("solicitation_number") or "",
            "agency": r.get("agency") or "", "office": r.get("office") or "", "naics": r.get("naics") or "",
            "psc": r.get("psc") or "", "set_aside": r.get("set_aside") or "", "due_date": r.get("response_deadline") or "",
            "status": r.get("status") or "", "source_url": r.get("ui_url") or "", "last_synced_at": now(),
            "tags": ["wfg/opportunity"],
        }, title, "\n".join(generated))
        paths.append(p)
    return paths


def sync_companies(c: sqlite3.Connection) -> list[Path]:
    paths = []
    q = """
    select s.id, s.legal_name, s.dba, s.website, s.notes, s.source, s.validation_date,
           group_concat(distinct cc.email) emails, group_concat(distinct cc.phone) phones,
           group_concat(distinct t.trade) trades, group_concat(distinct t.naics) naics,
           group_concat(distinct l.dedupe_key) opportunities,
           max(l.last_seen) last_seen, group_concat(distinct l.status) statuses
    from subcontractors s
    left join subcontractor_contacts cc on cc.subcontractor_id=s.id
    left join subcontractor_trades t on t.subcontractor_id=s.id
    left join subcontractor_opportunity_links l on l.subcontractor_id=s.id
    where coalesce(s.environment, 'production') = 'production'
      and coalesce(s.source, '') != 'manual-test'
    group by s.id order by s.legal_name
    """
    for r in c.execute(q):
        d = dict(r)
        p = VAULT / "03-Companies" / f"{slugify(d['legal_name'])}.md"
        interactions = [dict(x) for x in c.execute("select * from subcontractor_interactions where subcontractor_id=? order by datetime(coalesce(occurred_at,'1970-01-01')) desc limit 20", (d["id"],))]
        generated = [
            "## Profile",
            f"- Legal name: {d['legal_name']}",
            f"- Website: {d.get('website') or 'UNKNOWN'}",
            f"- Emails: {d.get('emails') or 'UNKNOWN'}",
            f"- Phones: {d.get('phones') or 'UNKNOWN'}",
            f"- Trades/NAICS: {d.get('trades') or 'UNKNOWN'} / {d.get('naics') or 'UNKNOWN'}",
            f"- Opportunity keys: {d.get('opportunities') or 'None'}",
            f"- Statuses: {d.get('statuses') or 'UNKNOWN'}",
            "", "## Communications",
        ]
        if interactions:
            for i in interactions:
                generated.append(f"- {i.get('occurred_at') or ''} — {i.get('direction')} {i.get('interaction_type')} / {i.get('status')} — `{i.get('dedupe_key') or ''}` — {i.get('subject') or ''}")
        else:
            generated.append("- No interactions recorded yet.")
        write_generated_note(p, {
            "type": "company", "subcontractor_id": d["id"], "legal_name": d["legal_name"], "dba": d.get("dba") or "",
            "website": d.get("website") or "", "primary_email": (d.get("emails") or "").split(",")[0] if d.get("emails") else "",
            "primary_phone": (d.get("phones") or "").split(",")[0] if d.get("phones") else "", "opportunities": [x for x in (d.get("opportunities") or "").split(",") if x],
            "last_contacted_at": d.get("last_seen") or "", "last_synced_at": now(), "tags": ["wfg/company", "wfg/subcontractor"],
        }, d["legal_name"], "\n".join(generated))
        paths.append(p)
    return paths


def sync_contacts(c: sqlite3.Connection) -> list[Path]:
    paths = []
    q = """
    select cc.*, s.legal_name from subcontractor_contacts cc
    left join subcontractors s on s.id=cc.subcontractor_id
    where coalesce(s.environment, 'production') = 'production'
      and coalesce(s.source, '') != 'manual-test'
    order by s.legal_name, cc.id
    """
    for r in c.execute(q):
        d = dict(r)
        name = d.get("name") if d.get("name") and d.get("name") != "UNKNOWN" else "Contact"
        title = f"{name} - {d.get('legal_name') or 'Unknown Company'}"
        p = VAULT / "04-Contacts" / f"contact-{d['id']}-{slugify(title)}.md"
        generated = "\n".join([
            "## Contact Details",
            f"- Company: [[{d.get('legal_name') or 'Unknown Company'}]]",
            f"- Name: {d.get('name') or 'UNKNOWN'}",
            f"- Role: {d.get('role') or 'UNKNOWN'}",
            f"- Email: {d.get('email') or 'UNKNOWN'}",
            f"- Phone: {d.get('phone') or 'UNKNOWN'}",
            f"- Source: {d.get('source') or 'UNKNOWN'}",
        ])
        write_generated_note(p, {"type": "contact", "contact_id": d["id"], "subcontractor_id": d.get("subcontractor_id") or "", "company": d.get("legal_name") or "", "email": d.get("email") or "", "phone": d.get("phone") or "", "last_synced_at": now(), "tags": ["wfg/contact"]}, title, generated)
        paths.append(p)
    return paths


def sync_emails(c: sqlite3.Connection) -> list[Path]:
    paths = []
    q = """
    select i.*, s.legal_name from subcontractor_interactions i
    left join subcontractors s on s.id=i.subcontractor_id
    where i.interaction_type in ('email','form','bounce')
      and coalesce(s.environment, 'production') = 'production'
      and coalesce(s.source, '') != 'manual-test'
    order by datetime(coalesce(i.occurred_at,'1970-01-01')) desc, i.id desc
    limit 2000
    """
    for r in c.execute(q):
        d = dict(r)
        date = (d.get("occurred_at") or now())[:10]
        company = d.get("legal_name") or f"sub-{d.get('subcontractor_id')}"
        title = f"{date} - {company} - {d.get('subject') or d.get('interaction_type') or 'interaction'}"
        p = VAULT / "05-Emails" / date[:4] / f"{slugify(title, 120)}.md"
        generated = "\n".join([
            "## Summary",
            f"- Company: [[{company}]]",
            f"- Opportunity: `{d.get('dedupe_key') or ''}`",
            f"- Direction: {d.get('direction') or ''}",
            f"- Type/status: {d.get('interaction_type') or ''} / {d.get('status') or ''}",
            f"- Occurred at: {d.get('occurred_at') or ''}",
            f"- Gmail message: {d.get('gmail_message_id') or d.get('external_id') or ''}",
            f"- Gmail thread: {d.get('gmail_thread_id') or ''}",
            f"- Match method: {d.get('match_method') or ''}",
            "", "## Notes", d.get("notes") or "",
        ])
        write_generated_note(p, {"type": "email", "interaction_id": d["id"], "direction": d.get("direction") or "", "status": d.get("status") or "", "dedupe_key": d.get("dedupe_key") or "", "subcontractor_id": d.get("subcontractor_id") or "", "company": company, "gmail_message_id": d.get("gmail_message_id") or d.get("external_id") or "", "gmail_thread_id": d.get("gmail_thread_id") or "", "subject": d.get("subject") or "", "occurred_at": d.get("occurred_at") or "", "last_synced_at": now(), "tags": ["wfg/email"]}, title, generated)
        paths.append(p)
    return paths


def write_dashboards() -> list[Path]:
    dashboards = {
        "WFG Command Center.md": """# WFG Command Center

## Active Opportunities
```dataview
TABLE status, due_date, agency, solicitation_number
FROM "01-Opportunities"
WHERE type = "opportunity" AND status != "closed"
SORT due_date ASC
```

## Recent Communications
```dataview
TABLE direction, status, company, occurred_at
FROM "05-Emails"
WHERE type = "email"
SORT occurred_at DESC
LIMIT 25
```

## Subcontractors
```dataview
TABLE primary_email, primary_phone, last_contacted_at, opportunities
FROM "03-Companies"
WHERE contains(tags, "wfg/subcontractor")
SORT last_contacted_at DESC
```
""",
        "Opportunity Pipeline.md": """# Opportunity Pipeline

```dataview
TABLE status, due_date, agency, naics, psc
FROM "01-Opportunities"
WHERE type = "opportunity"
SORT due_date ASC
```
""",
        "Subcontractor CRM.md": """# Subcontractor CRM

```dataview
TABLE legal_name, primary_email, primary_phone, opportunities, last_contacted_at
FROM "03-Companies"
WHERE type = "company"
SORT legal_name ASC
```
""",
        "Communications Inbox.md": """# Communications Inbox

```dataview
TABLE company, direction, status, dedupe_key, occurred_at, subject
FROM "05-Emails"
WHERE type = "email"
SORT occurred_at DESC
```
""",
    }
    paths = []
    for name, content in dashboards.items():
        p = VAULT / "00-Dashboards" / name
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    return paths


def sync_all() -> dict[str, Any]:
    ensure_dirs(); write_templates()
    c = con()
    paths = []
    paths += sync_opportunities(c)
    paths += sync_companies(c)
    paths += sync_contacts(c)
    paths += sync_emails(c)
    c.close()
    paths += write_dashboards()
    readme = VAULT / "README.md"
    readme.write_text(f"# Wright Foster Group Wiki\n\nGenerated/synced by Hermes at {now()}.\n\nStart at [[WFG Command Center]].\n", encoding="utf-8")
    paths.append(readme)
    archived = archive_stale_generated_notes(paths)
    return {"ok": True, "vault": str(VAULT), "notes_written": len(paths), "archived_stale_generated_notes": len(archived), "archived_paths": archived}


def verify() -> dict[str, Any]:
    required = [
        VAULT / "00-Dashboards" / "WFG Command Center.md",
        VAULT / "00-Dashboards" / "Subcontractor CRM.md",
        VAULT / "03-Companies",
        VAULT / "01-Opportunities",
    ]
    md_count = len(list(VAULT.glob("**/*.md"))) if VAULT.exists() else 0
    missing = [str(p) for p in required if not p.exists()]
    c = con()
    blank_links = c.execute("select count(*) from subcontractor_opportunity_links where coalesce(dedupe_key,'')='' and coalesce(opportunity_folder,'')!=''").fetchone()[0]
    c.close()
    return {"ok": not missing and blank_links == 0 and md_count > 0, "vault": str(VAULT), "markdown_notes": md_count, "missing": missing, "blank_subcontractor_opportunity_links": blank_links}


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-vault")
    sub.add_parser("sync-all")
    sub.add_parser("verify")
    args = p.parse_args()
    if args.cmd == "init-vault": ensure_dirs(); write_templates(); print(json.dumps({"ok": True, "vault": str(VAULT)}, indent=2)); return 0
    if args.cmd == "sync-all": print(json.dumps(sync_all(), indent=2)); return 0
    if args.cmd == "verify": print(json.dumps(verify(), indent=2)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
