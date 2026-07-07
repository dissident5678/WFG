#!/usr/bin/env python3
"""WFG tracking schema, outreach recording, and inbound email matching helpers.

This module keeps subcontractor/opportunity/email relationships normalized so
inbound communications can be tied back to the correct opportunity.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
from email.utils import parseaddr
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", PROJECT / "state" / "wfg_workflow.sqlite3")).resolve()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def columns(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in c.execute(f"pragma table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def add_column(c: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    if name not in columns(c, table):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_tracking_schema(c: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Create/extend tracking tables. Safe to run repeatedly."""
    own = c is None
    c = c or con()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS subcontractor_interactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subcontractor_id INTEGER NOT NULL,
          dedupe_key TEXT,
          interaction_type TEXT,
          status TEXT,
          direction TEXT,
          occurred_at TEXT,
          subject TEXT,
          local_path TEXT,
          external_id TEXT,
          notes TEXT
        );
        CREATE TABLE IF NOT EXISTS email_response_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gmail_message_id TEXT UNIQUE,
          thread_id TEXT,
          from_header TEXT,
          sender_email TEXT,
          subject TEXT,
          received_at TEXT,
          classification TEXT,
          reason TEXT,
          draft_id TEXT,
          draft_message_id TEXT,
          draft_subject TEXT,
          draft_created_at TEXT,
          snippet TEXT,
          raw_metadata_json TEXT
        );
        CREATE TABLE IF NOT EXISTS gmail_drafts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          draft_id TEXT,
          message_id TEXT,
          thread_id TEXT,
          to_recipients TEXT,
          cc_recipients TEXT,
          bcc_recipients TEXT,
          subject TEXT,
          body_source_path TEXT,
          attachments_json TEXT,
          created_at TEXT,
          gmail_url TEXT,
          status TEXT DEFAULT 'draft_created',
          notes TEXT
        );
        """
    )
    for name, decl in [
        ("contact_id", "INTEGER"),
        ("gmail_thread_id", "TEXT"),
        ("gmail_message_id", "TEXT"),
        ("gmail_rfc_message_id", "TEXT"),
        ("in_reply_to", "TEXT"),
        ("references_header", "TEXT"),
        ("match_method", "TEXT"),
        ("raw_metadata_json", "TEXT"),
    ]:
        add_column(c, "subcontractor_interactions", name, decl)
    for name, decl in [
        ("subcontractor_id", "INTEGER"),
        ("contact_id", "INTEGER"),
        ("opportunity_folder", "TEXT"),
        ("sent_message_id", "TEXT"),
        ("sent_thread_id", "TEXT"),
        ("sent_at", "TEXT"),
        ("gmail_rfc_message_id", "TEXT"),
    ]:
        add_column(c, "gmail_drafts", name, decl)
    for name, decl in [
        ("dedupe_key", "TEXT"),
        ("opportunity_folder", "TEXT"),
        ("subcontractor_id", "INTEGER"),
        ("contact_id", "INTEGER"),
        ("matched_outbound_interaction_id", "INTEGER"),
        ("match_method", "TEXT"),
        ("gmail_rfc_message_id", "TEXT"),
        ("in_reply_to", "TEXT"),
        ("references_header", "TEXT"),
    ]:
        add_column(c, "email_response_items", name, decl)
    c.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sub_interactions_gmail_thread ON subcontractor_interactions(gmail_thread_id);
        CREATE INDEX IF NOT EXISTS idx_sub_interactions_gmail_message ON subcontractor_interactions(gmail_message_id);
        CREATE INDEX IF NOT EXISTS idx_sub_interactions_sub_opp ON subcontractor_interactions(subcontractor_id, dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_gmail_drafts_thread ON gmail_drafts(thread_id);
        CREATE INDEX IF NOT EXISTS idx_gmail_drafts_sent_thread ON gmail_drafts(sent_thread_id);
        CREATE INDEX IF NOT EXISTS idx_gmail_drafts_sub_opp ON gmail_drafts(subcontractor_id, dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_gmail_drafts_to ON gmail_drafts(to_recipients);
        CREATE INDEX IF NOT EXISTS idx_email_response_items_thread ON email_response_items(thread_id);
        CREATE INDEX IF NOT EXISTS idx_email_response_items_sender ON email_response_items(sender_email);
        CREATE INDEX IF NOT EXISTS idx_email_response_items_sub_opp ON email_response_items(subcontractor_id, dedupe_key);
        """
    )
    if own:
        c.commit(); c.close()
    return {"ok": True, "db": str(DB)}


def opportunity_dedupe_for_folder(c: sqlite3.Connection, folder: str) -> str:
    r = c.execute(
        "select dedupe_key from opportunity_intakes where opportunity_folder=? order by id desc limit 1",
        (folder,),
    ).fetchone()
    if r and r["dedupe_key"]:
        return r["dedupe_key"]
    name = Path(folder).name
    m = re.match(r"([0-9a-f]{32})-", name)
    if m:
        r = c.execute(
            "select dedupe_key from opportunities where notice_id like ? order by last_seen desc limit 1",
            (m.group(1) + "%",),
        ).fetchone()
        if r and r["dedupe_key"]:
            return r["dedupe_key"]
        return "notice:" + m.group(1)
    return ""


def backfill_links(apply: bool = False) -> dict[str, Any]:
    c = con(); ensure_tracking_schema(c)
    candidates = []
    for r in c.execute(
        """select * from subcontractor_opportunity_links
           where coalesce(dedupe_key,'')='' and coalesce(opportunity_folder,'')!=''"""
    ):
        d = dict(r)
        dedupe = opportunity_dedupe_for_folder(c, d["opportunity_folder"])
        if dedupe:
            d["dedupe_key"] = dedupe
            candidates.append(d)
    merged = 0
    updated = 0
    if apply:
        for item in candidates:
            duplicate = c.execute(
                """select * from subcontractor_opportunity_links
                   where subcontractor_id=? and dedupe_key=? and coalesce(role,'')=coalesce(?,'') and id<>?
                   order by datetime(coalesce(last_seen, first_seen, '1970-01-01')) desc, id desc limit 1""",
                (item["subcontractor_id"], item["dedupe_key"], item.get("role"), item["id"]),
            ).fetchone()
            if duplicate:
                dup = dict(duplicate)
                merged_notes = "\n".join(x for x in [dup.get("notes") or "", item.get("notes") or ""] if x).strip()
                c.execute(
                    """update subcontractor_opportunity_links
                       set opportunity_folder=coalesce(nullif(opportunity_folder,''), ?),
                           status=coalesce(nullif(?,''), status),
                           first_seen=min(coalesce(first_seen, ?), coalesce(?, first_seen)),
                           last_seen=max(coalesce(last_seen, ?), coalesce(?, last_seen)),
                           notes=?
                       where id=?""",
                    (
                        item.get("opportunity_folder") or "",
                        item.get("status") or "",
                        item.get("first_seen") or now(),
                        item.get("first_seen") or now(),
                        item.get("last_seen") or now(),
                        item.get("last_seen") or now(),
                        merged_notes,
                        dup["id"],
                    ),
                )
                c.execute("delete from subcontractor_opportunity_links where id=?", (item["id"],))
                merged += 1
            else:
                c.execute("update subcontractor_opportunity_links set dedupe_key=?, last_seen=? where id=?", (item["dedupe_key"], now(), item["id"]))
                updated += 1
        c.commit()
    c.close()
    return {"apply": apply, "updated_or_would_update": len(candidates), "merged_duplicates": merged, "updated_rows": updated, "rows": candidates}


def contact_for_sub(c: sqlite3.Connection, subcontractor_id: int, email: str = "", phone: str = "") -> int | None:
    if email:
        r = c.execute("select id from subcontractor_contacts where subcontractor_id=? and lower(email)=lower(?) order by id desc limit 1", (subcontractor_id, email)).fetchone()
        if r: return int(r["id"])
    if phone:
        r = c.execute("select id from subcontractor_contacts where subcontractor_id=? and phone=? order by id desc limit 1", (subcontractor_id, phone)).fetchone()
        if r: return int(r["id"])
    r = c.execute("select id from subcontractor_contacts where subcontractor_id=? order by id desc limit 1", (subcontractor_id,)).fetchone()
    return int(r["id"]) if r else None


def find_subcontractor(c: sqlite3.Connection, company: str = "", email: str = "") -> dict[str, Any] | None:
    if email:
        r = c.execute(
            """select s.id subcontractor_id, cc.id contact_id, s.legal_name, cc.email
               from subcontractor_contacts cc join subcontractors s on s.id=cc.subcontractor_id
               where lower(cc.email)=lower(?) order by cc.id desc limit 1""",
            (email,),
        ).fetchone()
        if r: return dict(r)
    if company:
        r = c.execute("select id subcontractor_id, legal_name from subcontractors where lower(legal_name)=lower(?) order by id desc limit 1", (company,)).fetchone()
        if r:
            d = dict(r); d["contact_id"] = contact_for_sub(c, int(r["subcontractor_id"])) ; return d
    return None


def latest_opportunity_link(c: sqlite3.Connection, subcontractor_id: int, dedupe_key: str = "") -> dict[str, Any] | None:
    if dedupe_key:
        r = c.execute("select * from subcontractor_opportunity_links where subcontractor_id=? and dedupe_key=? order by id desc limit 1", (subcontractor_id, dedupe_key)).fetchone()
        if r: return dict(r)
    r = c.execute("select * from subcontractor_opportunity_links where subcontractor_id=? order by datetime(coalesce(last_seen,first_seen,'1970-01-01')) desc, id desc limit 1", (subcontractor_id,)).fetchone()
    return dict(r) if r else None


def record_interaction(
    *,
    subcontractor_id: int,
    dedupe_key: str,
    contact_id: int | None = None,
    interaction_type: str,
    status: str,
    direction: str,
    occurred_at: str | None = None,
    subject: str = "",
    local_path: str = "",
    external_id: str = "",
    notes: str = "",
    gmail_message_id: str = "",
    gmail_thread_id: str = "",
    gmail_rfc_message_id: str = "",
    in_reply_to: str = "",
    references_header: str = "",
    match_method: str = "manual_record",
    raw_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    c = con(); ensure_tracking_schema(c)
    occurred_at = occurred_at or now()
    link = latest_opportunity_link(c, subcontractor_id, dedupe_key)
    if link:
        c.execute("update subcontractor_opportunity_links set status=?, last_seen=?, notes=trim(coalesce(notes,'') || char(10) || ?) where id=?", (status, occurred_at, notes, link["id"]))
    c.execute(
        """insert into subcontractor_interactions(
             subcontractor_id,dedupe_key,contact_id,interaction_type,status,direction,occurred_at,subject,local_path,external_id,notes,
             gmail_message_id,gmail_thread_id,gmail_rfc_message_id,in_reply_to,references_header,match_method,raw_metadata_json)
           values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (subcontractor_id, dedupe_key, contact_id, interaction_type, status, direction, occurred_at, subject, local_path, external_id, notes,
         gmail_message_id, gmail_thread_id, gmail_rfc_message_id, in_reply_to, references_header, match_method, json.dumps(raw_metadata or {}, sort_keys=True)),
    )
    interaction_id = int(c.execute("select last_insert_rowid()").fetchone()[0])
    c.commit(); c.close()
    return {"ok": True, "interaction_id": interaction_id, "subcontractor_id": subcontractor_id, "dedupe_key": dedupe_key, "status": status}


def header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def match_inbound_to_crm(c: sqlite3.Connection, meta: dict, headers: list[dict], sender_email: str, subject: str) -> dict[str, Any]:
    ensure_tracking_schema(c)
    thread_id = meta.get("threadId") or ""
    msg_id = meta.get("id") or ""
    rfc = header(headers, "Message-ID")
    in_reply_to = header(headers, "In-Reply-To")
    refs = header(headers, "References")

    if thread_id:
        r = c.execute(
            """select i.id matched_outbound_interaction_id, i.subcontractor_id, i.contact_id, i.dedupe_key, l.opportunity_folder
               from subcontractor_interactions i
               left join subcontractor_opportunity_links l on l.subcontractor_id=i.subcontractor_id and l.dedupe_key=i.dedupe_key
               where i.gmail_thread_id=? and i.direction='outbound'
               order by i.id desc limit 1""",
            (thread_id,),
        ).fetchone()
        if r:
            d = dict(r); d.update({"match_method": "gmail_thread_id", "gmail_rfc_message_id": rfc, "in_reply_to": in_reply_to, "references_header": refs}); return d
        r = c.execute(
            """select gd.subcontractor_id, gd.contact_id, gd.dedupe_key, gd.opportunity_folder, gd.id matched_outbound_interaction_id
               from gmail_drafts gd
               where (gd.sent_thread_id=? or gd.thread_id=?) and coalesce(gd.subcontractor_id,'')!=''
               order by gd.id desc limit 1""",
            (thread_id, thread_id),
        ).fetchone()
        if r:
            d = dict(r); d.update({"match_method": "gmail_thread_id_gmail_drafts", "gmail_rfc_message_id": rfc, "in_reply_to": in_reply_to, "references_header": refs}); return d

    if in_reply_to or refs:
        needles = [x for x in [in_reply_to] + refs.split() if x]
        for needle in needles:
            r = c.execute(
                """select i.id matched_outbound_interaction_id, i.subcontractor_id, i.contact_id, i.dedupe_key, l.opportunity_folder
                   from subcontractor_interactions i left join subcontractor_opportunity_links l on l.subcontractor_id=i.subcontractor_id and l.dedupe_key=i.dedupe_key
                   where i.gmail_rfc_message_id=? order by i.id desc limit 1""",
                (needle,),
            ).fetchone()
            if r:
                d = dict(r); d.update({"match_method": "rfc_reply_header", "gmail_rfc_message_id": rfc, "in_reply_to": in_reply_to, "references_header": refs}); return d

    sender = (sender_email or "").lower()
    if sender:
        r = c.execute(
            """select s.id subcontractor_id, cc.id contact_id, l.dedupe_key, l.opportunity_folder, l.id link_id
               from subcontractor_contacts cc
               join subcontractors s on s.id=cc.subcontractor_id
               left join subcontractor_opportunity_links l on l.subcontractor_id=s.id
               where lower(cc.email)=lower(?)
               order by datetime(coalesce(l.last_seen,l.first_seen,'1970-01-01')) desc, l.id desc limit 1""",
            (sender,),
        ).fetchone()
        if r:
            d = dict(r); d.update({"matched_outbound_interaction_id": None, "match_method": "sender_email", "gmail_rfc_message_id": rfc, "in_reply_to": in_reply_to, "references_header": refs}); return d

    return {"dedupe_key": "", "opportunity_folder": "", "subcontractor_id": None, "contact_id": None, "matched_outbound_interaction_id": None, "match_method": "unmatched", "gmail_rfc_message_id": rfc, "in_reply_to": in_reply_to, "references_header": refs}


def record_cli(args: argparse.Namespace) -> dict[str, Any]:
    c = con(); ensure_tracking_schema(c)
    sub = find_subcontractor(c, company=args.company, email=args.email)
    if not sub:
        raise SystemExit(f"Could not resolve subcontractor for company={args.company!r} email={args.email!r}")
    link = latest_opportunity_link(c, int(sub["subcontractor_id"]), args.dedupe_key)
    dedupe = args.dedupe_key or (link or {}).get("dedupe_key") or ""
    if not dedupe:
        raise SystemExit("Could not resolve opportunity dedupe_key; pass --dedupe-key")
    contact_id = args.contact_id or sub.get("contact_id")
    c.close()
    return record_interaction(
        subcontractor_id=int(sub["subcontractor_id"]),
        contact_id=int(contact_id) if contact_id else None,
        dedupe_key=dedupe,
        interaction_type=args.interaction_type,
        status=args.status,
        direction=args.direction,
        occurred_at=args.occurred_at or now(),
        subject=args.subject,
        local_path=args.local_path,
        external_id=args.external_id,
        notes=args.notes,
        gmail_message_id=args.gmail_message_id,
        gmail_thread_id=args.gmail_thread_id,
        match_method=args.match_method or "manual_record",
    )


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    a = sub.add_parser("backfill-links"); a.add_argument("--apply", action="store_true")
    a = sub.add_parser("record")
    a.add_argument("--company", default="")
    a.add_argument("--email", default="")
    a.add_argument("--contact-id", type=int, default=0)
    a.add_argument("--dedupe-key", default="")
    a.add_argument("--interaction-type", required=True, choices=["email", "form", "phone", "note", "bounce"])
    a.add_argument("--status", required=True)
    a.add_argument("--direction", default="outbound")
    a.add_argument("--occurred-at", default="")
    a.add_argument("--subject", default="")
    a.add_argument("--local-path", default="")
    a.add_argument("--external-id", default="")
    a.add_argument("--gmail-message-id", default="")
    a.add_argument("--gmail-thread-id", default="")
    a.add_argument("--match-method", default="manual_record")
    a.add_argument("--notes", default="")
    args = p.parse_args()
    if args.cmd == "migrate": print(json.dumps(ensure_tracking_schema(), indent=2)); return 0
    if args.cmd == "backfill-links": print(json.dumps(backfill_links(args.apply), indent=2)); return 0
    if args.cmd == "record": print(json.dumps(record_cli(args), indent=2)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
