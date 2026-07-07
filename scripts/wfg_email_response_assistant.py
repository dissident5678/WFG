#!/usr/bin/env python3
"""WFG Gmail response assistant.

Checks Marcus/WFG Gmail for new actionable business-operation emails, filters
junk/ads/newsletters/no-response messages, drafts suggested replies in Gmail's
Drafts tab, and prints a Telegram-friendly notification summary.

It never sends email. Drafts are created in-thread when possible.

Cadence policy is enforced internally so one cron can run hourly:
- Never check email between 9:00 PM and 7:00 AM America/New_York.
- Weekdays 7:00 AM-5:00 PM America/New_York: process every 60 minutes.
- Weekends and weekday off-hours before 9:00 PM: process every 4 hours.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import email.message
import html
import json
import os
import re
import sqlite3
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wfg_tracking_schema import ensure_tracking_schema, match_inbound_to_crm, record_interaction

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
DB = PROJECT / 'state' / 'wfg_workflow.sqlite3'
TOKEN = Path(os.environ.get('GOOGLE_TOKEN_PATH', '/home/nick/.hermes/google_token.json'))
ACCOUNT = os.environ.get('WFG_GMAIL_FROM', 'wrightfostergroup@gmail.com').lower()
TZ = ZoneInfo('America/New_York')
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]
JUNK_LABELS = {'SPAM', 'TRASH', 'CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL', 'CATEGORY_FORUMS'}
NO_REPLY_PAT = re.compile(r'(no[-_ ]?reply|donotreply|do[-_ ]?not[-_ ]?reply|notifications?|mailer-daemon|postmaster)@', re.I)
AD_PAT = re.compile(r'\b(unsubscribe|sale|discount|webinar|newsletter|digest|promotion|promotional|limited time|marketing|advertisement|sponsored|click here|view in browser|optimize your|online business banking)\b', re.I)
MARKETING_SENDER_PAT = re.compile(r'(^|[.@_-])(mktg|marketing|promo|promotions|news|newsletter)([.@_-]|$)', re.I)
BUSINESS_PAT = re.compile(r'\b(wright foster|wfg|contract|solicitation|sam\.gov|quote|rfq|rfi|proposal|bid|subcontract|subcontractor|janitorial|cleaning|insurance|bank|invoice|payment|registration|uei|sam registration|government|usda|ars|fort detrick|frederick|capability|scope|pricing|estimate|schedule|site visit|question|award|purchase order|po\b|agreement|llc|tax|ein|vendor|supplier)\b', re.I)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def now_et() -> dt.datetime:
    return dt.datetime.now(TZ)


def is_quiet_hours(t: dt.datetime | None = None) -> bool:
    t = t or now_et()
    return t.hour >= 21 or t.hour < 7


def cadence_minutes(t: dt.datetime | None = None) -> int:
    t = t or now_et()
    if is_quiet_hours(t):
        return 0
    is_weekday = t.weekday() < 5
    in_business_hours = 7 <= t.hour < 17
    return 60 if is_weekday and in_business_hours else 240


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript('''
    CREATE TABLE IF NOT EXISTS email_response_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_at TEXT,
      cadence_minutes INTEGER,
      processed_count INTEGER,
      actionable_count INTEGER,
      drafts_created INTEGER,
      summary_json TEXT
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
    ''')
    ensure_tracking_schema(c)
    return c


def should_run(force: bool = False) -> bool:
    current = now_et()
    if is_quiet_hours(current) and not force:
        return False
    if force:
        return True
    cadence = cadence_minutes(current)
    if cadence <= 0:
        return False
    c = con()
    r = c.execute('select run_at, cadence_minutes from email_response_runs where processed_count is not null order by id desc limit 1').fetchone()
    c.close()
    if not r:
        return True
    last = dt.datetime.fromisoformat(r['run_at'])
    due = last + dt.timedelta(minutes=cadence)
    return dt.datetime.now(dt.timezone.utc) >= due


def service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN), scopes=SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get('name', '').lower() == name.lower():
            return h.get('value', '')
    return ''


def body_text(payload: dict) -> str:
    chunks: list[str] = []
    def walk(part: dict):
        mime = part.get('mimeType', '')
        data = part.get('body', {}).get('data')
        if data and mime in {'text/plain', 'text/html'}:
            try:
                txt = base64.urlsafe_b64decode(data.encode()).decode('utf-8', 'ignore')
                if mime == 'text/html':
                    txt = re.sub(r'<(br|p|div|li)[^>]*>', '\n', txt, flags=re.I)
                    txt = re.sub(r'<[^>]+>', ' ', txt)
                    txt = html.unescape(txt)
                chunks.append(txt)
            except Exception:
                pass
        for child in part.get('parts', []) or []:
            walk(child)
    walk(payload)
    txt = '\n'.join(chunks)
    txt = re.sub(r'\n{3,}', '\n\n', txt)
    return txt.strip()[:6000]


def classify(meta: dict, text: str) -> tuple[str, str]:
    labels = set(meta.get('labelIds', []) or [])
    headers = meta.get('payload', {}).get('headers', [])
    from_h = header(headers, 'From')
    sender = parseaddr(from_h)[1].lower()
    subject = header(headers, 'Subject')
    list_unsub = header(headers, 'List-Unsubscribe')
    precedence = header(headers, 'Precedence')
    auto_sub = header(headers, 'Auto-Submitted')
    combined = f'{from_h}\n{subject}\n{text[:2000]}'
    if sender == ACCOUNT:
        return 'no_response', 'sent by WFG account'
    if labels & JUNK_LABELS:
        return 'junk_or_ad', 'Gmail category/spam/trash label'
    if NO_REPLY_PAT.search(sender) or auto_sub.lower().startswith('auto-'):
        return 'no_response', 'automated/no-reply sender'
    if MARKETING_SENDER_PAT.search(sender) and (list_unsub or AD_PAT.search(combined)):
        return 'junk_or_ad', 'marketing sender/list email'
    if list_unsub and AD_PAT.search(combined):
        return 'junk_or_ad', 'newsletter/advertising list'
    if precedence.lower() in {'bulk', 'list', 'junk'} and not BUSINESS_PAT.search(combined):
        return 'junk_or_ad', 'bulk/list email without business signal'
    if BUSINESS_PAT.search(combined):
        return 'business_actionable', 'business-operations keyword/source match'
    # Human direct emails with questions deserve review unless they look promotional.
    if '?' in text[:1200] and not AD_PAT.search(combined):
        return 'business_actionable', 'direct question from non-automated sender'
    return 'no_response', 'no business-response signal'


def clean_inbound_text(text: str) -> str:
    """Return the useful top portion of an inbound email, excluding quoted history/signatures."""
    text = re.sub(r'\r', '', text or '')
    cut_patterns = [
        r'\nOn .+ wrote:\n', r'\nFrom:\s.+\nSent:', r'\n-----Original Message-----',
        r'\n>+', r'\n_{5,}', r'\n--\s*\n', r'\nBest regards[,\s]*\n', r'\nSincerely[,\s]*\n',
    ]
    first_cut = len(text)
    for pat in cut_patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            first_cut = min(first_cut, m.start())
    text = text[:first_cut]
    text = re.sub(r'https?://\S+', '[link]', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:1600]


def sender_first_name(from_name: str) -> str:
    name = (from_name or '').strip().strip('"')
    if not name or '@' in name:
        return ''
    # Drop company-ish suffixes and keep the human first name.
    name = re.sub(r'\b(team|sales|info|support|admin|estimating|office)\b.*', '', name, flags=re.I).strip()
    return name.split()[0].strip(',') if name else ''


def extract_questions(text: str, limit: int = 2) -> list[str]:
    cleaned = clean_inbound_text(text)
    parts = re.split(r'(?<=[?.!])\s+', cleaned)
    questions = [p.strip() for p in parts if '?' in p and 8 <= len(p.strip()) <= 220]
    return questions[:limit]


def extract_amounts(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'\$\s?\d[\d,]*(?:\.\d{2})?', text or '')))[:3]


def extract_dates(text: str) -> list[str]:
    pat = r'\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Oct\.?|Nov\.?|Dec\.?)\s+\d{1,2}(?:,\s*\d{4})?|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b'
    return list(dict.fromkeys(re.findall(pat, text or '', flags=re.I)))[:3]


def draft_body(from_name: str, subject: str, original_text: str, reason: str) -> str:
    """Create a contextual, non-generic draft reply.

    Policy: never emit the old generic acknowledgement. Every draft must reflect
    the sender's actual message by referencing a concrete question, decision,
    amount, date, scope item, or next action from the inbound email. If the
    message is unclear, draft a specific clarification request instead of a bland
    receipt acknowledgement.
    """
    first = sender_first_name(from_name)
    opener = f'Hi {first},' if first else 'Hello,'
    combined = f'{subject}\n{original_text}'
    cleaned = clean_inbound_text(original_text)
    questions = extract_questions(original_text)
    amounts = extract_amounts(original_text)
    dates = extract_dates(original_text)
    lower = combined.lower()

    paragraphs: list[str] = []
    closing_request = ''

    if re.search(r"\b(can'?t|cannot|unable|not able|won't be able|outside (our )?scope|do not provide|don't provide|not a fit|no longer interested)\b", lower):
        reason_detail = ""
        if re.search(r'\b(in[- ]?home|home estimate|residential|house cleaning|maid)\b', lower):
            reason_detail = " I also noted that your services appear to be residential/in-home rather than the commercial/federal facility scope we need."
        paragraphs.append(f"Thank you for letting us know. I understand this is not a fit for your team based on the scope of work.{reason_detail}")
        paragraphs.append("We will mark your company as not available for this opportunity so we do not keep following up on the same request.")
        closing_request = "If I misunderstood and there is a different commercial/government-services contact we should speak with, please feel free to point me in the right direction."
    elif re.search(r'\b(in[- ]?home|home estimate|residential|house cleaning|maid)\b', lower):
        paragraphs.append("Thanks for following up. To clarify, our request is not for residential or in-home service.")
        paragraphs.append("We are looking for a subcontractor that handles the commercial/federal facility scope described in the opportunity packet.")
        closing_request = "If that type of work is outside your services, no problem — just let us know and we will remove you from this opportunity list."
    elif amounts or re.search(r'\b(attached|proposal|estimate|quote|pricing|price)\b', lower):
        detail = f" I see pricing/detail references including {', '.join(amounts)}." if amounts else ""
        paragraphs.append(f"Thank you for sending this over.{detail}")
        paragraphs.append("I will review the pricing, assumptions, exclusions, and schedule against the opportunity requirements before we decide how to carry it forward.")
        if questions:
            paragraphs.append(f"I also noted your question: “{questions[0]}”")
            closing_request = "I will confirm that point before we rely on the pricing."
        else:
            closing_request = "If there are any exclusions, minimums, or access/site-visit assumptions that are not already included, please send those over as well."
    elif questions:
        paragraphs.append("Thanks for the question. I want to make sure we answer it accurately instead of guessing from the solicitation summary.")
        for q in questions:
            paragraphs.append(f"Question noted: “{q}”")
        closing_request = "I will verify this against the opportunity documents and follow up with a specific answer or clarification request."
    elif re.search(r'\b(site visit|walk[- ]?through|walkthrough|tour|visit)\b', lower):
        when = f" I noted the date reference: {', '.join(dates)}." if dates else ""
        paragraphs.append(f"Thanks for raising the site-visit/walkthrough point.{when}")
        paragraphs.append("That affects pricing, so I will check the opportunity file and confirm what is available before we tell anyone to assume a site visit.")
        closing_request = "If you have specific site-access requirements or preferred times, please send them over."
    elif re.search(r'\b(insurance|coi|certificate|license|licensed|bond|bonding|w-9|w9|sam|uei)\b', lower):
        paragraphs.append("Thanks — I see this relates to vendor documentation/eligibility rather than just pricing.")
        paragraphs.append("I will review the document requirement and make sure we only request or use what is actually needed at this stage.")
        closing_request = "If there is an expiration date, coverage limit, license number, or restriction we should note, please point that out."
    else:
        # Still make the draft specific by quoting a short inbound summary.
        excerpt = cleaned[:240].strip()
        if len(cleaned) > 240:
            excerpt += '...'
        paragraphs.append("Thanks for reaching out. I want to make sure I respond to the right point from your message.")
        if excerpt:
            paragraphs.append(f"I read your note as: “{excerpt}”")
        paragraphs.append("I will review this in context and respond with the specific next step rather than a generic acknowledgement.")
        closing_request = "If there is one decision or deadline you need from us first, please point me to that item."

    body = '\n\n'.join([opener, *paragraphs, closing_request, 'Thank you,', 'Nick Wright\nWright Foster Group LLC\n410-490-8681\nwrightfostergroup@gmail.com'])
    return f'''{body}

---
Internal draft note for Nick: Marcus classified this as business-actionable because: {reason}. This draft was generated with the personalized-response policy; review/edit before sending.'''


def create_reply_draft(svc, meta: dict, text: str, reason: str) -> dict:
    headers = meta.get('payload', {}).get('headers', [])
    from_h = header(headers, 'From')
    from_name, sender = parseaddr(from_h)
    subject = header(headers, 'Subject') or '(no subject)'
    if not subject.lower().startswith('re:'):
        subject = 'Re: ' + subject
    msg = email.message.EmailMessage()
    msg['To'] = from_h
    msg['From'] = ACCOUNT
    msg['Subject'] = subject
    msg['In-Reply-To'] = header(headers, 'Message-ID')
    refs = header(headers, 'References')
    if refs or header(headers, 'Message-ID'):
        msg['References'] = (refs + ' ' + header(headers, 'Message-ID')).strip()
    msg.set_content(draft_body(from_name, subject, text, reason))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {'message': {'raw': raw, 'threadId': meta.get('threadId')}}
    draft = svc.users().drafts().create(userId='me', body=body).execute()
    got = svc.users().drafts().get(userId='me', id=draft['id'], format='metadata').execute()
    return {
        'draft_id': draft.get('id'),
        'message_id': draft.get('message', {}).get('id') or got.get('message', {}).get('id'),
        'thread_id': draft.get('message', {}).get('threadId') or meta.get('threadId'),
        'subject': subject,
        'verified': got.get('id') == draft.get('id'),
    }


def list_candidate_ids(svc, lookback_days: int, max_messages: int) -> list[str]:
    query = f'in:inbox newer_than:{lookback_days}d -in:drafts -in:sent'
    res = svc.users().messages().list(userId='me', q=query, maxResults=max_messages).execute()
    return [m['id'] for m in res.get('messages', [])]


def process(force: bool = False, lookback_days: int = 14, max_messages: int = 25, dry_run: bool = False) -> dict:
    current = now_et()
    cadence = cadence_minutes(current)
    if is_quiet_hours(current) and not force:
        return {'skipped': True, 'reason': 'quiet hours: no email checks between 9 PM and 7 AM ET', 'cadence_minutes': 0, 'local_time': current.isoformat()}
    if not should_run(force):
        return {'skipped': True, 'reason': 'not due yet', 'cadence_minutes': cadence, 'local_time': current.isoformat()}
    svc = service()
    c = con()
    ids = list_candidate_ids(svc, lookback_days, max_messages)
    processed = actionable = drafts = 0
    created: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for mid in ids:
        if c.execute('select 1 from email_response_items where gmail_message_id=?', (mid,)).fetchone():
            continue
        meta = svc.users().messages().get(userId='me', id=mid, format='full').execute()
        headers = meta.get('payload', {}).get('headers', [])
        text = body_text(meta.get('payload', {}))
        classification, reason = classify(meta, text)
        from_h = header(headers, 'From')
        sender = parseaddr(from_h)[1].lower()
        subject = header(headers, 'Subject')
        date_h = header(headers, 'Date')
        match = match_inbound_to_crm(c, meta, headers, sender, subject)
        processed += 1
        draft = {}
        if classification == 'business_actionable':
            actionable += 1
            if not dry_run:
                draft = create_reply_draft(svc, meta, text, reason)
                drafts += 1
            created.append({'from': from_h, 'subject': subject, 'reason': reason, 'draft_id': draft.get('draft_id'), 'message_id': mid, 'match_method': match.get('match_method'), 'dedupe_key': match.get('dedupe_key')})
        else:
            ignored.append({'from': from_h, 'subject': subject, 'classification': classification, 'reason': reason})
        raw_meta = {'labelIds': meta.get('labelIds', []), 'date': date_h, 'match': match}
        c.execute('''insert or ignore into email_response_items(gmail_message_id,thread_id,from_header,sender_email,subject,received_at,classification,reason,draft_id,draft_message_id,draft_subject,draft_created_at,snippet,raw_metadata_json,dedupe_key,opportunity_folder,subcontractor_id,contact_id,matched_outbound_interaction_id,match_method,gmail_rfc_message_id,in_reply_to,references_header)
                     values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            mid, meta.get('threadId'), from_h, sender, subject, date_h, classification, reason,
            draft.get('draft_id'), draft.get('message_id'), draft.get('subject'), now_utc() if draft else None,
            meta.get('snippet'), json.dumps(raw_meta, sort_keys=True), match.get('dedupe_key'), match.get('opportunity_folder'),
            match.get('subcontractor_id'), match.get('contact_id'), match.get('matched_outbound_interaction_id'), match.get('match_method'),
            match.get('gmail_rfc_message_id'), match.get('in_reply_to'), match.get('references_header'),
        ))
        if match.get('subcontractor_id') and not dry_run:
            record_interaction(
                subcontractor_id=int(match['subcontractor_id']), dedupe_key=match.get('dedupe_key') or '',
                contact_id=match.get('contact_id'), interaction_type='email',
                status='reply_drafted' if draft else classification, direction='inbound', occurred_at=date_h or now_utc(),
                subject=subject, external_id=mid, notes=f"Inbound Gmail classified {classification}: {reason}",
                gmail_message_id=mid, gmail_thread_id=meta.get('threadId') or '', gmail_rfc_message_id=match.get('gmail_rfc_message_id') or '',
                in_reply_to=match.get('in_reply_to') or '', references_header=match.get('references_header') or '',
                match_method=match.get('match_method') or 'unmatched', raw_metadata=raw_meta,
            )
    summary = {'created': created, 'ignored_count': len(ignored), 'processed': processed, 'actionable': actionable, 'drafts': drafts, 'cadence_minutes': cadence, 'dry_run': dry_run, 'local_time': current.isoformat()}
    c.execute('insert into email_response_runs(run_at,cadence_minutes,processed_count,actionable_count,drafts_created,summary_json) values(?,?,?,?,?,?)', (now_utc(), cadence, processed, actionable, drafts, json.dumps(summary, sort_keys=True)))
    c.commit(); c.close()
    return summary


def format_notification(summary: dict) -> str:
    if summary.get('skipped') or not summary.get('created'):
        return ''
    lines = [f"## WFG email response assistant", '', f"Created {summary['drafts']} Gmail draft response(s) for business-actionable email(s).", '']
    for item in summary['created']:
        lines += [f"- From: {item['from']}", f"  Subject: {item['subject']}", f"  Draft ID: `{item.get('draft_id')}`", f"  Reason: {item['reason']}"]
    lines += ['', 'Review in Gmail Drafts: https://mail.google.com/mail/u/0/#drafts', '', 'Nothing was sent.']
    return '\n'.join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--force', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--lookback-days', type=int, default=14)
    p.add_argument('--max-messages', type=int, default=25)
    p.add_argument('--json', action='store_true')
    args = p.parse_args()
    summary = process(args.force, args.lookback_days, args.max_messages, args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        msg = format_notification(summary)
        if msg:
            print(msg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
