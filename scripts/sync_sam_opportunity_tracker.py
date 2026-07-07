#!/usr/bin/env python3
"""Sync archived SAM.gov opportunity search results into a deduplicated Google Sheet (v2).

v2 changes:
- Imports gating/scoring/dedupe from sam_morning_opportunity_brief.py (same
  directory) so the tracker and the morning brief can never drift apart.
- Only target-fit rows (bucket pursue/sources_sought/watch) are written to the
  sheet; off-target spare-parts noise no longer fills the tracker.
- raw_json is truncated to stay under Google's 50,000-char cell limit.
- Adds a bucket column value into fit_reasons prefix for filtering.

Creates/updates the Wright Foster Group opportunity tracker. Deduplication key
is noticeId when available, falling back to solicitationNumber/title/link.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sam_morning_opportunity_brief as brief  # noqa: E402
import wfg_phase1  # noqa: E402

from google.oauth2.credentials import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
ARCHIVE = PROJECT / 'opportunity-searches' / 'sam-api'
CONFIG_PATH = PROJECT / 'opportunity-searches' / 'sam-api-google-sheet.json'
TOKEN_PATH = Path('/home/nick/.hermes/google_token.json')
CLIENT_SECRET_PATH = Path('/home/nick/.hermes/google_client_secret.json')
TITLE = 'WFG SAM.gov Opportunity Tracker'
SUMMARY_SHEET = 'Summary'
ORGANIZED_SHEET = 'Organized Opportunities'
NO_DEADLINE_SHEET = 'No Listed Deadline'
# Legacy sheet names retained for read compatibility only. Nick reorganized
# the live tracker into Summary / Organized Opportunities / No Listed Deadline;
# do not recreate or overwrite the old flat Opportunities/Metadata tabs.
LEGACY_OPPORTUNITY_SHEET = 'Opportunities'
LEGACY_META_SHEET = 'Metadata'
RAW_JSON_MAX_CHARS = 1500

HEADERS = [
    'first_seen_file', 'first_seen_date', 'last_seen_file', 'last_seen_date', 'times_seen',
    'dedupe_key', 'notice_id', 'solicitation_number', 'title', 'agency_path', 'agency_code',
    'posted_date', 'opportunity_type', 'base_type', 'archive_type', 'archive_date',
    'set_aside_description', 'set_aside_code', 'naics_code', 'naics_codes', 'psc_code',
    'active', 'response_deadline', 'deadline_days_from_sync', 'estimated_value_or_magnitude',
    'score', 'fit_reasons', 'watch_outs', 'office_city', 'office_state', 'office_zip',
    'office_country', 'place_of_performance', 'poc_names', 'poc_emails', 'poc_phones',
    'description_link', 'additional_info_link', 'sam_ui_link', 'resource_links', 'source_files',
    'raw_json'
]
ORGANIZED_HEADERS = ['state_group', 'response_due_date', 'days_until_due'] + HEADERS[:-1]
KEEP_DEADLINE_DAYS_MIN = 14
CANONICAL_APPEND_RULE = (
    'Canonical update rule: never paste or append opportunities to the bottom of a tab. '
    'Run this tracker sync, which rebuilds the grouped tabs and places each data row under its matching STATE block.'
)


def load_credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise SystemExit(f'Missing Google token: {TOKEN_PATH}')
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    if not creds.valid and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return creds


def service(name: str, version: str):
    return build(name, version, credentials=load_credentials())


def compact_json(value: Any) -> str:
    if value is None:
        return ''
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def file_date(path: Path) -> str:
    m = re.search(r'raw-(\d{8})-', path.name)
    if not m:
        return ''
    return f'{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}'


def load_archived(batch_dir: Path | None = None) -> dict[str, dict]:
    records: dict[str, dict] = {}
    paths = []
    if batch_dir is not None:
        manifest = wfg_phase1.read_manifest(batch_dir)
        paths = [batch_dir / rel for rel in manifest.get('raw_files', [])]
    else:
        paths = sorted(ARCHIVE.glob('raw-*.json'))
    for path in paths:
        try:
            data = json.loads(path.read_text(errors='replace'))
        except Exception:
            continue
        items = data.get('opportunitiesData') or data.get('data') or []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = brief.dedupe_key(item)
            rec = records.get(key)
            if not rec:
                rec = {
                    'item': item,
                    'first_seen_file': path.name,
                    'first_seen_date': file_date(path),
                    'last_seen_file': path.name,
                    'last_seen_date': file_date(path),
                    'source_files': [],
                    'times_seen': 0,
                }
                records[key] = rec
            rec['item'] = item  # keep latest visible fields
            rec['last_seen_file'] = path.name
            rec['last_seen_date'] = file_date(path)
            rec['source_files'].append(path.name)
            rec['times_seen'] += 1
    return records


def format_pop(item: dict) -> str:
    pop = item.get('placeOfPerformance')
    if isinstance(pop, dict):
        return compact_json(pop)
    return str(pop or item.get('placeOfPerformanceCity') or '').strip()


def pocs(item: dict) -> tuple[str, str, str]:
    raw = item.get('pointOfContact') or []
    if isinstance(raw, dict):
        raw = [raw]
    names, emails, phones = [], [], []
    for p in raw:
        if not isinstance(p, dict):
            continue
        if p.get('fullName'):
            names.append(str(p.get('fullName')))
        if p.get('email'):
            emails.append(str(p.get('email')))
        if p.get('phone'):
            phones.append(str(p.get('phone')))
    return '; '.join(names), '; '.join(emails), '; '.join(phones)


def row_for(key: str, rec: dict, sheet_row: int, bucket: str, score: int,
            reasons: list[str], watch: list[str], profile: dict, descriptions: dict) -> list[str | int]:
    item = rec['item']
    office = item.get('officeAddress') if isinstance(item.get('officeAddress'), dict) else {}
    poc_names, poc_emails, poc_phones = pocs(item)
    ev = brief.estimated_value(item, descriptions)
    raw_json = compact_json(item)
    if len(raw_json) > RAW_JSON_MAX_CHARS:
        raw_json = raw_json[:RAW_JSON_MAX_CHARS] + '...truncated'
    deadline_formula = f'=IF(W{sheet_row}="","",IFERROR(INT(DATEVALUE(LEFT(W{sheet_row},10))-TODAY()),""))'
    return [
        rec['first_seen_file'], rec['first_seen_date'], rec['last_seen_file'], rec['last_seen_date'], rec['times_seen'],
        key, item.get('noticeId', ''), item.get('solicitationNumber', ''), item.get('title', ''),
        item.get('fullParentPathName', ''), item.get('fullParentPathCode', ''), item.get('postedDate', ''),
        item.get('type', ''), item.get('baseType', ''), item.get('archiveType', ''), item.get('archiveDate', ''),
        item.get('typeOfSetAsideDescription', ''), item.get('typeOfSetAside', ''), item.get('naicsCode', ''),
        ', '.join(item.get('naicsCodes') or []) if isinstance(item.get('naicsCodes'), list) else str(item.get('naicsCodes') or ''),
        item.get('classificationCode', ''), item.get('active', ''), item.get('responseDeadLine', '') or item.get('responseDeadline', ''),
        deadline_formula, ev if ev is not None else '', score,
        f'[{bucket}] ' + '; '.join(reasons), '; '.join(watch), office.get('city', ''), office.get('state', ''), office.get('zipcode', ''),
        office.get('countryCode', ''), format_pop(item), poc_names, poc_emails, poc_phones,
        str(item.get('description', '')), item.get('additionalInfoLink', ''), item.get('uiLink', ''),
        compact_json(item.get('resourceLinks') or item.get('links')), ', '.join(sorted(set(rec['source_files']))),
        raw_json,
    ]


def parse_deadline_date(item: dict) -> dt.date | None:
    raw = item.get('responseDeadLine') or item.get('responseDeadline') or item.get('response_date')
    if not raw:
        return None
    s = str(raw).strip().replace('Z', '+00:00')
    try:
        d = dt.datetime.fromisoformat(s)
    except Exception:
        d = None
        for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
            try:
                d = dt.datetime.strptime(s[:10], fmt)
                break
            except Exception:
                continue
    if d is None:
        return None
    if d.tzinfo:
        d = d.astimezone().replace(tzinfo=None)
    return d.date()


def state_group(item: dict) -> str:
    """Place-of-performance state first; office state fallback."""
    st = brief.pop_state(item)
    if st:
        return st
    office = item.get('officeAddress') if isinstance(item.get('officeAddress'), dict) else {}
    return str(office.get('state') or 'UNKNOWN').upper()


def hyperlink_formula(url: Any, label: str) -> str:
    url = str(url or '').strip()
    if not url:
        return ''
    return f'=HYPERLINK("{url.replace(chr(34), chr(34) + chr(34))}","{label}")'


def clean_cell(value: Any) -> str | int | float:
    """Sheets values.update expects scalars; None inside a row causes ragged writes."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def clean_rows(values: list[list[Any]]) -> list[list[str | int | float]]:
    return [[clean_cell(cell) for cell in row] for row in values]


def organized_row_for(key: str, rec: dict, row_index: int, bucket: str, score: int,
                      reasons: list[str], watch: list[str], profile: dict,
                      descriptions: dict) -> list[str | int]:
    item = rec['item']
    base = row_for(key, rec, row_index, bucket, score, reasons, watch, profile, descriptions)[:-1]
    due_date = parse_deadline_date(item)
    days = brief.deadline_days(item)
    due = due_date.isoformat() if due_date else ''
    # Keep the organized sheets useful in the browser: description/SAM links are
    # clickable formulas, while the plain URL remains recoverable from the raw
    # archived JSON files listed in source_files.
    base[36] = hyperlink_formula(item.get('description'), 'View description')
    base[38] = hyperlink_formula(item.get('uiLink'), 'Open SAM.gov')
    return [state_group(item), due, days if days is not None else ''] + base


def apply_days_until_due_formulas(values: list[list[str | int]]) -> None:
    """Make visible days columns recalculate daily in the live Sheet.

    Organized layout columns:
    - B: response_due_date (normalized date)
    - C: days_until_due (visible field Nick scans/sorts)
    - Z: original SAM response_deadline string
    - AA: deadline_days_from_sync compatibility field
    """
    for sheet_row, row in enumerate(values, start=1):
        if is_data_row(row):
            if len(row) > 2 and row[1]:
                row[2] = f'=IF(B{sheet_row}="","",INT(B{sheet_row}-TODAY()))'
            elif len(row) > 2:
                row[2] = ''
            if len(row) > 26 and row[25]:
                row[26] = f'=IF(Z{sheet_row}="","",IFERROR(INT(DATEVALUE(LEFT(Z{sheet_row},10))-TODAY()),""))'
            elif len(row) > 26:
                row[26] = ''


def is_data_row(row: list[Any]) -> bool:
    return len(row) > 8 and str(row[8]).startswith(('notice:', 'sol:', 'fallback:'))


def validate_grouped_layout(values: list[list[Any]], tab_name: str) -> list[str]:
    """Return layout errors that would make the tracker confusing in Sheets.

    The tracker is intentionally state-blocked for human scanning. Any future
    agent/sync change must keep every opportunity inside the matching STATE
    block, with no orphan/malformed data rows or duplicate dedupe keys.
    """
    errors: list[str] = []
    current_state = None
    seen_keys: set[str] = set()
    last_state = ''
    for row_num, row in enumerate(values, start=1):
        first = str(row[0]) if row else ''
        if first.startswith('STATE: '):
            current_state = first.split('STATE: ', 1)[1].split('|', 1)[0].strip()
            if last_state and current_state < last_state:
                errors.append(f'{tab_name} row {row_num}: state block {current_state} is out of order after {last_state}')
            last_state = current_state
            continue
        if row == ORGANIZED_HEADERS or not any(str(cell).strip() for cell in row):
            continue
        if is_data_row(row):
            row_state = str(row[0] or 'UNKNOWN')
            key = str(row[8])
            if current_state is None:
                errors.append(f'{tab_name} row {row_num}: data row appears before a STATE block')
            elif row_state != current_state:
                errors.append(f'{tab_name} row {row_num}: {key} is under STATE {current_state} but row state is {row_state}')
            if key in seen_keys:
                errors.append(f'{tab_name} row {row_num}: duplicate dedupe_key {key}')
            seen_keys.add(key)
            continue
        # Allow title/note rows before the first state block only.
        if current_state is not None:
            errors.append(f'{tab_name} row {row_num}: malformed non-data row inside STATE {current_state}: {row[:3]}')
    return errors


def verify_live_sheet_layout(sheets, sid: str) -> list[str]:
    errors: list[str] = []
    for tab in (ORGANIZED_SHEET, NO_DEADLINE_SHEET):
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"'{tab}'!A1:AR",
            valueRenderOption='FORMULA',
        ).execute()
        errors.extend(validate_grouped_layout(res.get('values', []), tab))
    return errors


def ensure_sheets(sheets, sid: str) -> dict[str, int]:
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields='sheets(properties(sheetId,title,index))').execute()
    ids = {s['properties']['title']: s['properties']['sheetId'] for s in meta.get('sheets', [])}
    requests = []
    for title in (SUMMARY_SHEET, ORGANIZED_SHEET, NO_DEADLINE_SHEET):
        if title not in ids:
            requests.append({'addSheet': {'properties': {'title': title}}})
    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={'requests': requests}).execute()
        meta = sheets.spreadsheets().get(spreadsheetId=sid, fields='sheets(properties(sheetId,title,index))').execute()
        ids = {s['properties']['title']: s['properties']['sheetId'] for s in meta.get('sheets', [])}
    return ids


def grouped_rows(title: str, note: str, rows: list[list[str | int]]) -> list[list[str | int]]:
    out: list[list[str | int]] = [[title], [note], []]
    current = None
    group: list[list[str | int]] = []
    for row in sorted(rows, key=lambda r: (str(r[0]), 999999 if r[2] == '' else int(r[2]), str(r[11]))):
        state = str(row[0] or 'UNKNOWN')
        if current is None:
            current = state
        if state != current:
            out.append([f'STATE: {current}  |  {len(group)} opportunities'])
            out.append(ORGANIZED_HEADERS)
            out.extend(group)
            out.append([])
            current = state
            group = []
        group.append(row)
    if current is not None:
        out.append([f'STATE: {current}  |  {len(group)} opportunities'])
        out.append(ORGANIZED_HEADERS)
        out.extend(group)
    return out


def summary_rows(total: int, kept_rows: list[list[str | int]], removed_under_14: int,
                 no_deadline_rows: list[list[str | int]]) -> list[list[str | int]]:
    by_state: dict[str, dict[str, Any]] = {}
    for row in kept_rows:
        st = str(row[0] or 'UNKNOWN')
        due = str(row[1] or '')
        entry = by_state.setdefault(st, {'count': 0, 'earliest': ''})
        entry['count'] += 1
        if due and (not entry['earliest'] or due < entry['earliest']):
            entry['earliest'] = due
    rows: list[list[str | int]] = [
        ['WFG SAM.gov Opportunity Tracker — Filtered 14+ Days Out'],
        [],
        ['Current date used', dt.date.today().isoformat()],
        ['Original opportunity rows', total],
        [f'Kept: due in {KEEP_DEADLINE_DAYS_MIN}+ days', len(kept_rows)],
        [f'Removed: due in under {KEEP_DEADLINE_DAYS_MIN} days', removed_under_14],
        ['Separated: no listed response deadline', len(no_deadline_rows)],
        ['State source rule', 'Place of performance state first; office state fallback'],
        ['Agent append procedure', CANONICAL_APPEND_RULE],
        [],
        ['State', 'Kept Opportunities', 'Earliest Due Date'],
    ]
    for st in sorted(by_state):
        rows.append([st, by_state[st]['count'], by_state[st]['earliest']])
    return rows


def load_or_create_spreadsheet() -> tuple[str, str]:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            cfg = {}
    sid = os.environ.get('WFG_SAM_OPPORTUNITY_SHEET_ID') or cfg.get('spreadsheetId')
    sheets = service('sheets', 'v4')
    if sid:
        meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        return sid, meta.get('spreadsheetUrl', f'https://docs.google.com/spreadsheets/d/{sid}/edit')
    body = {'properties': {'title': TITLE}, 'sheets': [
        {'properties': {'title': SUMMARY_SHEET}},
        {'properties': {'title': ORGANIZED_SHEET}},
        {'properties': {'title': NO_DEADLINE_SHEET}},
    ]}
    meta = sheets.spreadsheets().create(body=body, fields='spreadsheetId,spreadsheetUrl').execute()
    sid = meta['spreadsheetId']
    url = meta['spreadsheetUrl']
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({'spreadsheetId': sid, 'spreadsheetUrl': url, 'title': TITLE}, indent=2))
    return sid, url


def update_sheet(records: dict[str, dict], dry_run: bool = False, simulate_interrupt_before_write: bool = False) -> tuple[str, str, int, int, int, int]:
    sid = 'DRY_RUN'
    url = 'DRY_RUN'
    sheets = None
    ids = {}
    if not dry_run and not simulate_interrupt_before_write:
        sid, url = load_or_create_spreadsheet()
        sheets = service('sheets', 'v4')
        ids = ensure_sheets(sheets, sid)
    profile = brief.load_profile()
    descriptions = brief.load_desc_cache()

    kept_rows: list[list[str | int]] = []
    no_deadline_rows: list[list[str | int]] = []
    removed_under_14 = 0
    removed_off_target = 0
    total_target_fit = 0

    for key, rec in sorted(records.items(), key=lambda kv: (kv[1]['last_seen_date'], kv[1]['item'].get('title', ''))):
        bucket, score, reasons, watch = brief.classify(rec['item'], profile, descriptions)
        if bucket == 'reject':
            removed_off_target += 1
            continue
        total_target_fit += 1
        row = organized_row_for(key, rec, 2 + total_target_fit, bucket, score, reasons, watch, profile, descriptions)
        days = row[2]
        if days == '':
            no_deadline_rows.append(row)
        elif int(days) < KEEP_DEADLINE_DAYS_MIN:
            removed_under_14 += 1
        else:
            kept_rows.append(row)

    summary = summary_rows(total_target_fit, kept_rows, removed_under_14, no_deadline_rows)
    organized = grouped_rows(
        'Organized Opportunities — Due in 14+ Days',
        'Filter: listed response deadlines 14+ calendar days out; under-14-day rows removed; sorted by state, then days until due.',
        kept_rows,
    )
    no_deadline = grouped_rows(
        'No Listed Response Deadline — Manual Review',
        'No listed response_deadline, so days until due could not be calculated. Check these manually in SAM.gov.',
        no_deadline_rows,
    )
    apply_days_until_due_formulas(organized)
    apply_days_until_due_formulas(no_deadline)
    summary = clean_rows(summary)
    organized = clean_rows(organized)
    no_deadline = clean_rows(no_deadline)
    layout_errors = (
        validate_grouped_layout(organized, ORGANIZED_SHEET)
        + validate_grouped_layout(no_deadline, NO_DEADLINE_SHEET)
    )
    if layout_errors:
        raise RuntimeError('tracker layout validation failed before write: ' + '; '.join(layout_errors[:20]))

    if dry_run:
        return sid, url, len(kept_rows), removed_under_14, len(no_deadline_rows), removed_off_target
    if simulate_interrupt_before_write:
        raise RuntimeError('simulated interrupted tracker sync before any sheet clear/update')

    # Remove any old manual/header merges before writing values. If a data row
    # lands on a merged A:AR row, Google reports 44 updated cells but only the
    # first cell is actually readable, which looks like stray ['MD'] rows.
    if ids:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={'requests': [
                {'unmergeCells': {'range': {'sheetId': ids[title], 'startColumnIndex': 0, 'endColumnIndex': len(ORGANIZED_HEADERS)}}}
                for title in (SUMMARY_SHEET, ORGANIZED_SHEET, NO_DEADLINE_SHEET)
            ]},
        ).execute()

    # Prepare all replacement values before clearing any human-facing tab. If a failure is
    # detected in preparation, the prior Sheet remains untouched.
    for title, values, clear_range in (
        (SUMMARY_SHEET, summary, 'A:Z'),
        (ORGANIZED_SHEET, organized, 'A:AR'),
        (NO_DEADLINE_SHEET, no_deadline, 'A:AR'),
    ):
        sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{title}'!{clear_range}").execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"'{title}'!A1",
            valueInputOption='USER_ENTERED',
            body={'values': values},
        ).execute()

    # Basic formatting matching Nick's organized layout: tab order, wrapped rows,
    # bold titles/state headers/repeated header rows, and frozen top title rows.
    requests: list[dict[str, Any]] = []
    for index, title in enumerate((SUMMARY_SHEET, ORGANIZED_SHEET, NO_DEADLINE_SHEET)):
        sid_sheet = ids[title]
        requests.append({'updateSheetProperties': {'properties': {'sheetId': sid_sheet, 'index': index}, 'fields': 'index'}})
        requests.append({'updateSheetProperties': {'properties': {'sheetId': sid_sheet, 'gridProperties': {'frozenRowCount': 2}}, 'fields': 'gridProperties.frozenRowCount'}})
        requests.append({'repeatCell': {'range': {'sheetId': sid_sheet}, 'cell': {'userEnteredFormat': {'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat.wrapStrategy'}})
        requests.append({'repeatCell': {'range': {'sheetId': sid_sheet, 'startRowIndex': 0, 'endRowIndex': 1}, 'cell': {'userEnteredFormat': {'textFormat': {'bold': True, 'fontSize': 12}}}, 'fields': 'userEnteredFormat.textFormat'}})
    for title, values in ((ORGANIZED_SHEET, organized), (NO_DEADLINE_SHEET, no_deadline)):
        sid_sheet = ids[title]
        for idx, row in enumerate(values):
            if row and (str(row[0]).startswith('STATE: ') or row == ORGANIZED_HEADERS):
                requests.append({'repeatCell': {'range': {'sheetId': sid_sheet, 'startRowIndex': idx, 'endRowIndex': idx + 1}, 'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}}, 'fields': 'userEnteredFormat.textFormat.bold'}})
    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={'requests': requests}).execute()

    return sid, url, len(kept_rows), removed_under_14, len(no_deadline_rows), removed_off_target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--print-seen-keys', action='store_true', help='Print dedupe keys currently in the Google Sheet, one per line.')
    parser.add_argument('--sync', action='store_true', help='Sync archive to Google Sheet.')
    parser.add_argument('--current-batch', action='store_true', help='Sync only the current completed batch.')
    parser.add_argument('--batch-id', help='Sync a specific batch id.')
    parser.add_argument('--dry-run', action='store_true', help='Build rows but do not write Google Sheet.')
    parser.add_argument('--simulate-interrupt-before-write', action='store_true', help='Test failure before any Sheet mutation.')
    parser.add_argument('--verify-sheet-layout', action='store_true', help='Read the live Sheet and fail if rows are outside matching STATE blocks.')
    args = parser.parse_args()
    if args.verify_sheet_layout:
        sid, url = load_or_create_spreadsheet()
        sheets = service('sheets', 'v4')
        errors = verify_live_sheet_layout(sheets, sid)
        print(json.dumps({'spreadsheetId': sid, 'spreadsheetUrl': url, 'layout_ok': not errors, 'errors': errors[:50]}, indent=2))
        return 0 if not errors else 1
    if args.print_seen_keys:
        sid, _ = load_or_create_spreadsheet()
        sheets = service('sheets', 'v4')
        seen: set[str] = set()
        # New organized layout: dedupe_key is column I, with repeated state/header rows.
        for sheet_name in (ORGANIZED_SHEET, NO_DEADLINE_SHEET):
            try:
                res = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{sheet_name}'!I:I").execute()
            except Exception:
                continue
            for row in res.get('values', []):
                if row and str(row[0]).startswith(('notice:', 'sol:', 'fallback:')):
                    seen.add(str(row[0]))
        # Backward compatibility if an old flat Opportunities tab still exists.
        try:
            res = sheets.spreadsheets().values().get(spreadsheetId=sid, range=f"'{LEGACY_OPPORTUNITY_SHEET}'!F2:F").execute()
            for row in res.get('values', []):
                if row and row[0]:
                    seen.add(str(row[0]))
        except Exception:
            pass
        for key in sorted(seen):
            print(key)
        return 0
    if args.sync or args.current_batch or args.batch_id or not any(vars(args).values()):
        batch_id = args.batch_id
        batch_dir = None
        if args.current_batch:
            batch_id, batch_dir, _ = wfg_phase1.current_completed_batch_dir()
        elif args.batch_id:
            batch_dir = wfg_phase1.BATCHES / args.batch_id
        records = load_archived(batch_dir)
        if batch_dir:
            wfg_phase1.mark(batch_dir, tracker_sync_status='running')
        sid, url, count, removed_under_14, no_deadline_count, removed_off_target = update_sheet(records, dry_run=args.dry_run, simulate_interrupt_before_write=args.simulate_interrupt_before_write)
        if batch_dir and not args.dry_run:
            wfg_phase1.mark(batch_dir, tracker_sync_status='completed')
        print(json.dumps({
            'spreadsheetId': sid,
            'spreadsheetUrl': url,
            'kept_due_in_14_plus_days': count,
            'removed_due_under_14_days': removed_under_14,
            'no_listed_response_deadline': no_deadline_count,
            'removed_off_target': removed_off_target,
            'raw_files_count': len(list(ARCHIVE.glob('raw-*.json'))),
        }, indent=2))
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
