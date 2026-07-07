#!/usr/bin/env python3
"""Create and track Gmail drafts for WFG email drafts.

Global WFG rule: when an email is drafted, also create a real Gmail draft so
Nick can review it in Gmail's Drafts tab. Creating a draft is non-binding; this
script never sends email.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import email.message
import json
import mimetypes
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from wfg_tracking_schema import ensure_tracking_schema

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
DB = PROJECT / 'state' / 'wfg_workflow.sqlite3'
TOKEN = Path(os.environ.get('GOOGLE_TOKEN_PATH', '/home/nick/.hermes/google_token.json'))
DEFAULT_FROM = os.environ.get('WFG_GMAIL_FROM', 'wrightfostergroup@gmail.com')
VALID_EMAIL_RE = re.compile(r'^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$', re.I)
INVALID_RECIPIENT_MARKERS = ('to verify', 'do not send', 'contact form', 'placeholder', 'unknown', 'n/a', 'none', '[', ']')


def validate_recipient_list(value: str, field: str) -> None:
    if not value:
        return
    for item in re.split(r'[;,]', value):
        email = item.strip()
        if not email:
            continue
        low = email.lower()
        if any(marker in low for marker in INVALID_RECIPIENT_MARKERS) or not VALID_EMAIL_RE.match(email):
            raise SystemExit(f'Invalid {field} recipient blocked before Gmail draft creation: {email}')


SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def service():
    creds = Credentials.from_authorized_user_file(str(TOKEN), scopes=SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS gmail_drafts(
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
    )''')
    ensure_tracking_schema(c)
    return c


def extract_markdown_email(path: Path) -> dict[str, str]:
    text = path.read_text(errors='ignore')
    to = cc = bcc = subject = ''
    body_lines: list[str] = []
    in_body = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith('to:') and not to:
            to = stripped.split(':', 1)[1].strip()
            continue
        if stripped.lower().startswith('cc:') and not cc:
            cc = stripped.split(':', 1)[1].strip()
            continue
        if stripped.lower().startswith('bcc:') and not bcc:
            bcc = stripped.split(':', 1)[1].strip()
            continue
        if stripped.lower().startswith('subject:') and not subject:
            subject = stripped.split(':', 1)[1].strip()
            continue
        if re.match(r'^##\s+Draft email\s*$', stripped, re.I):
            in_body = True
            continue
        if in_body:
            body_lines.append(line)
    body = '\n'.join(body_lines).strip() or text
    # Strip local warning headings from Gmail body but keep substantive content.
    body = re.sub(r'^# .*$\n?', '', body, count=1, flags=re.M).strip()
    body = re.sub(r'^\*\*DO NOT SEND[^\n]*\n+', '', body, flags=re.I | re.M).strip()
    return {'to': to, 'cc': cc, 'bcc': bcc, 'subject': subject, 'body': body}


def add_attachments(msg: email.message.EmailMessage, attachments: Iterable[Path]) -> None:
    for p in attachments:
        if not p.exists():
            raise FileNotFoundError(p)
        ctype, _ = mimetypes.guess_type(str(p))
        maintype, subtype = (ctype or 'application/octet-stream').split('/', 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)


def build_message(to: str, subject: str, body: str, cc: str = '', bcc: str = '', sender: str = DEFAULT_FROM, attachments: Iterable[Path] = ()) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg['To'] = to
    if cc:
        msg['Cc'] = cc
    if bcc:
        msg['Bcc'] = bcc
    msg['From'] = sender
    msg['Subject'] = subject
    msg.set_content(body)
    add_attachments(msg, attachments)
    return msg


def gmail_draft_url(draft_id: str) -> str:
    # Gmail does not expose a stable public URL for a draft through the API; this
    # opens the Drafts mailbox where the draft is visible.
    return 'https://mail.google.com/mail/u/0/#drafts'


def create_draft(to: str, subject: str, body: str, cc: str = '', bcc: str = '', sender: str = DEFAULT_FROM, attachments: Iterable[Path] = (), dedupe_key: str = '', body_source_path: str = '', notes: str = '') -> dict:
    validate_recipient_list(to, 'To')
    validate_recipient_list(cc, 'Cc')
    validate_recipient_list(bcc, 'Bcc')
    svc = service()
    msg = build_message(to, subject, body, cc, bcc, sender, attachments)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = svc.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
    draft_id = draft.get('id')
    message = draft.get('message', {})
    # Verify by reading back the created draft.
    verified = svc.users().drafts().get(userId='me', id=draft_id, format='metadata').execute()
    out = {
        'draft_id': draft_id,
        'message_id': message.get('id') or verified.get('message', {}).get('id'),
        'thread_id': message.get('threadId') or verified.get('message', {}).get('threadId'),
        'to': to,
        'cc': cc,
        'bcc': bcc,
        'subject': subject,
        'created_at': now(),
        'gmail_url': gmail_draft_url(draft_id),
        'verified': bool(verified.get('id') == draft_id),
    }
    c = con()
    c.execute('''insert into gmail_drafts(dedupe_key,draft_id,message_id,thread_id,to_recipients,cc_recipients,bcc_recipients,subject,body_source_path,attachments_json,created_at,gmail_url,status,notes)
                 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        dedupe_key, out['draft_id'], out['message_id'], out['thread_id'], to, cc, bcc, subject,
        body_source_path, json.dumps([str(Path(p)) for p in attachments]), out['created_at'], out['gmail_url'],
        'draft_created_verified' if out['verified'] else 'draft_created_unverified', notes,
    ))
    if dedupe_key:
        c.execute('insert into workflow_events(dedupe_key,event_type,event_at,actor,details_json) values(?,?,?,?,?)', (dedupe_key, 'gmail_draft_created', out['created_at'], 'wfg_gmail_drafts', json.dumps(out, sort_keys=True)))
    c.commit(); c.close()
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('create')
    a.add_argument('--to', required=True)
    a.add_argument('--subject', required=True)
    a.add_argument('--body', required=True)
    a.add_argument('--cc', default='')
    a.add_argument('--bcc', default='')
    a.add_argument('--dedupe-key', default='')
    a.add_argument('--body-source-path', default='')
    a.add_argument('--notes', default='')
    a.add_argument('--attachment', action='append', default=[])
    a = sub.add_parser('from-md')
    a.add_argument('path')
    a.add_argument('--to', default='')
    a.add_argument('--subject', default='')
    a.add_argument('--dedupe-key', default='')
    a.add_argument('--notes', default='')
    a.add_argument('--attachment', action='append', default=[])
    a = sub.add_parser('list-recent')
    a.add_argument('--limit', type=int, default=10)
    args = p.parse_args()
    if args.cmd == 'create':
        out = create_draft(args.to, args.subject, args.body, args.cc, args.bcc, DEFAULT_FROM, [Path(x) for x in args.attachment], args.dedupe_key, args.body_source_path, args.notes)
        print(json.dumps(out, indent=2)); return 0
    if args.cmd == 'from-md':
        data = extract_markdown_email(Path(args.path))
        to = args.to or data['to']
        subject = args.subject or data['subject']
        if not to or not subject:
            raise SystemExit('from-md requires To and Subject in markdown or via flags')
        out = create_draft(to, subject, data['body'], data['cc'], data['bcc'], DEFAULT_FROM, [Path(x) for x in args.attachment], args.dedupe_key, str(Path(args.path).resolve()), args.notes)
        print(json.dumps(out, indent=2)); return 0
    if args.cmd == 'list-recent':
        c = con()
        rows = [dict(r) for r in c.execute('select * from gmail_drafts order by id desc limit ?', (args.limit,))]
        c.close(); print(json.dumps(rows, indent=2)); return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
