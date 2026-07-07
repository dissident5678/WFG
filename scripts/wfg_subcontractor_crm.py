#!/usr/bin/env python3
"""WFG subcontractor CRM automation.

Purpose:
- Persist every subcontractor candidate discovered during opportunity work.
- Track trades, NAICS, service areas, contacts, websites, evidence/source files,
  opportunity links, quote/outreach facts, and verification status.
- Reuse existing subcontractors for future opportunities before scraping again.

Safety:
- Local database/file automation only. This script does not contact vendors,
  send emails, approve subs, approve prices, or submit bids.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
DB = Path(os.environ.get('WFG_DB_PATH', PROJECT / 'state' / 'wfg_workflow.sqlite3')).resolve()
OPPS = PROJECT / 'opportunities'
REPORTS = PROJECT / 'subcontractors'


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')[:90] or 'unknown'


def norm_phone(phone: str) -> str:
    digits = re.sub(r'\D+', '', phone or '')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


def norm_domain(url: str) -> str:
    url = (url or '').strip().lower()
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
    return url.split('/')[0]


def norm_name(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).strip()
    stop = {'llc', 'inc', 'incorporated', 'co', 'company', 'corp', 'corporation', 'the'}
    words = [w for w in s.split() if w not in stop]
    return ' '.join(words) or s


def parse_state_city(location: str) -> tuple[str, str, str]:
    loc = location or ''
    city = county = state = ''
    # Handles common Google Places format: "412 Pine Ave, Frederick, MD 21701, USA"
    parts = [p.strip() for p in loc.split(',')]
    if len(parts) >= 2:
        city = parts[-3] if len(parts) >= 3 else parts[-2]
    m = re.search(r'\b([A-Z]{2})\s+\d{5}', loc)
    if m:
        state = m.group(1)
    county_map = {
        'frederick': 'Frederick County',
        'middletown': 'Frederick County',
        'jefferson': 'Frederick County',
        'eldersburg': 'Carroll County',
        'hagerstown': 'Washington County',
        'gaithersburg': 'Montgomery County',
    }
    county = county_map.get(city.lower(), '')
    return state, county, city


def migrate() -> None:
    c = con()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS subcontractors(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      legal_name TEXT NOT NULL,
      dba TEXT,
      website TEXT,
      notes TEXT,
      exclusions_concerns TEXT,
      source TEXT,
      validation_date TEXT,
      environment TEXT DEFAULT 'production'
    );
    CREATE TABLE IF NOT EXISTS subcontractor_contacts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER,
      name TEXT,
      role TEXT,
      email TEXT,
      phone TEXT,
      source TEXT,
      environment TEXT DEFAULT 'production'
    );
    CREATE TABLE IF NOT EXISTS subcontractor_trades(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER,
      trade TEXT,
      naics TEXT,
      status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')) DEFAULT 'unverified',
      source TEXT,
      environment TEXT DEFAULT 'production'
    );
    CREATE TABLE IF NOT EXISTS subcontractor_geography(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER,
      state TEXT,
      county TEXT,
      city TEXT,
      radius_miles INTEGER,
      status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')) DEFAULT 'unverified',
      environment TEXT DEFAULT 'production'
    );
    CREATE TABLE IF NOT EXISTS subcontractor_credentials(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER,
      kind TEXT,
      name TEXT,
      identifier TEXT,
      status TEXT CHECK(status in ('verified','unverified','expired','not_applicable')),
      expiration_date TEXT,
      source TEXT,
      notes TEXT,
      environment TEXT DEFAULT 'production'
    );
    CREATE TABLE IF NOT EXISTS subcontractor_sources(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER NOT NULL,
      source_type TEXT,
      source_path TEXT,
      source_url TEXT,
      source_hash TEXT,
      observed_at TEXT,
      raw_json TEXT,
      notes TEXT,
      UNIQUE(subcontractor_id, source_hash)
    );
    CREATE TABLE IF NOT EXISTS subcontractor_opportunity_links(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      subcontractor_id INTEGER NOT NULL,
      dedupe_key TEXT,
      opportunity_folder TEXT,
      role TEXT,
      status TEXT,
      first_seen TEXT,
      last_seen TEXT,
      notes TEXT,
      UNIQUE(subcontractor_id, dedupe_key, role)
    );
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
    CREATE TABLE IF NOT EXISTS subcontractor_search_cache(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      query_trade TEXT,
      query_state TEXT,
      query_county TEXT,
      query_city TEXT,
      result_json TEXT,
      searched_at TEXT,
      UNIQUE(query_trade, query_state, query_county, query_city)
    );
    CREATE INDEX IF NOT EXISTS idx_subcontractor_contacts_phone ON subcontractor_contacts(phone);
    CREATE INDEX IF NOT EXISTS idx_subcontractor_contacts_email ON subcontractor_contacts(email);
    CREATE INDEX IF NOT EXISTS idx_subcontractor_trades_trade ON subcontractor_trades(trade, naics);
    CREATE INDEX IF NOT EXISTS idx_subcontractor_geo ON subcontractor_geography(state, county, city);
    CREATE INDEX IF NOT EXISTS idx_sub_opp_links ON subcontractor_opportunity_links(dedupe_key, status);
    ''')
    c.commit(); c.close()


def source_hash(row: dict, source_path: Path | None) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
    if source_path:
        h.update(str(source_path).encode())
    return h.hexdigest()


def find_existing(c: sqlite3.Connection, row: dict) -> int | None:
    name_key = norm_name(row.get('company') or row.get('legal_name') or '')
    phone = norm_phone(row.get('phone') or '')
    domain = norm_domain(row.get('website') or '')
    candidates = []
    for r in c.execute('select id, legal_name, website from subcontractors'):
        score = 0
        if norm_name(r['legal_name']) == name_key:
            score += 3
        if domain and norm_domain(r['website'] or '') == domain:
            score += 3
        if phone:
            pr = c.execute('select 1 from subcontractor_contacts where subcontractor_id=? and phone like ?', (r['id'], f'%{phone[-7:]}%')).fetchone()
            if pr:
                score += 2
        if score >= 3:
            candidates.append((score, r['id']))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def upsert_candidate(row: dict, source_path: Path | None = None, dedupe_key: str = '', opp_folder: str = '') -> int:
    migrate()
    c = con()
    name = (row.get('company') or row.get('legal_name') or '').strip()
    if not name:
        c.close(); raise ValueError('candidate row missing company/legal_name')
    sid = find_existing(c, row)
    website = (row.get('website') or '').strip()
    notes_bits = [row.get('source_detail') or '', row.get('notes') or '']
    if sid is None:
        cur = c.execute(
            'insert into subcontractors(legal_name, website, notes, source, validation_date) values(?,?,?,?,?)',
            (name, website, '\n'.join(x for x in notes_bits if x), row.get('source') or 'candidate_import', now()),
        )
        sid = int(cur.lastrowid)
    else:
        c.execute(
            'update subcontractors set website=coalesce(nullif(?,\'\'), website), notes=trim(coalesce(notes,\'\') || char(10) || ?), validation_date=? where id=?',
            (website, '\n'.join(x for x in notes_bits if x), now(), sid),
        )
    phone = (row.get('phone') or '').strip()
    email = (row.get('email') or '').strip()
    if phone or (email and 'TO VERIFY' not in email):
        exists = c.execute('select id from subcontractor_contacts where subcontractor_id=? and coalesce(phone,\'\')=? and coalesce(email,\'\')=?', (sid, phone, email if 'TO VERIFY' not in email else '')).fetchone()
        if not exists:
            c.execute('insert into subcontractor_contacts(subcontractor_id,name,role,email,phone,source) values(?,?,?,?,?,?)', (sid, row.get('contact') or 'UNKNOWN', 'UNKNOWN', email if 'TO VERIFY' not in email else '', phone, row.get('source') or 'candidate_import'))
    trade = row.get('trade') or row.get('scope') or 'janitorial/custodial services'
    naics = row.get('naics') or ('561720' if re.search(r'janitorial|cleaning|custodial', trade, re.I) else '')
    if not c.execute('select id from subcontractor_trades where subcontractor_id=? and lower(trade)=lower(?)', (sid, trade)).fetchone():
        c.execute('insert into subcontractor_trades(subcontractor_id,trade,naics,status,source) values(?,?,?,?,?)', (sid, trade, naics, 'unverified', row.get('source') or 'candidate_import'))
    state, county, city = parse_state_city(row.get('location') or '')
    if state or city or county:
        if not c.execute('select id from subcontractor_geography where subcontractor_id=? and coalesce(state,\'\')=? and coalesce(county,\'\')=? and coalesce(city,\'\')=?', (sid, state, county, city)).fetchone():
            c.execute('insert into subcontractor_geography(subcontractor_id,state,county,city,radius_miles,status) values(?,?,?,?,?,?)', (sid, state, county, city, 50, 'unverified'))
    if source_path:
        sh = source_hash(row, source_path)
        c.execute('insert or ignore into subcontractor_sources(subcontractor_id,source_type,source_path,source_url,source_hash,observed_at,raw_json,notes) values(?,?,?,?,?,?,?,?)', (sid, row.get('source') or 'candidate_csv', str(source_path), row.get('google_maps_url') or row.get('website') or '', sh, now(), json.dumps(row, sort_keys=True), row.get('source_detail') or ''))
    if dedupe_key or opp_folder:
        if not dedupe_key and opp_folder:
            dedupe_key = infer_dedupe_from_folder(Path(opp_folder))
        c.execute('insert into subcontractor_opportunity_links(subcontractor_id,dedupe_key,opportunity_folder,role,status,first_seen,last_seen,notes) values(?,?,?,?,?,?,?,?) on conflict(subcontractor_id,dedupe_key,role) do update set last_seen=excluded.last_seen,status=excluded.status,notes=excluded.notes', (sid, dedupe_key, opp_folder, 'candidate', row.get('status') or 'candidate_not_contacted', now(), now(), row.get('source_detail') or ''))
    c.commit(); c.close()
    return sid


def infer_dedupe_from_folder(folder: Path) -> str:
    m = re.match(r'([0-9a-f]{32})-', folder.name)
    if m:
        # Folder uses truncated-ish prefix in some WFG data; try DB lookup by folder path first, then notice prefix.
        c = con()
        r = c.execute('select dedupe_key from opportunity_intakes where opportunity_folder=? order by id desc limit 1', (str(folder),)).fetchone()
        if r:
            c.close(); return r['dedupe_key']
        r = c.execute('select dedupe_key from opportunities where notice_id like ? order by last_seen desc limit 1', (m.group(1)+'%',)).fetchone()
        c.close()
        if r:
            return r['dedupe_key']
        return 'notice:' + m.group(1)
    return ''


def import_csv(path: Path, dedupe_key: str = '', opp_folder: str = '') -> list[int]:
    path = path.resolve()
    if not dedupe_key:
        try:
            idx = path.parts.index('opportunities')
            opp_folder_path = Path(*path.parts[:idx+2])
            dedupe_key = infer_dedupe_from_folder(opp_folder_path)
            opp_folder = str(opp_folder_path)
        except ValueError:
            pass
    ids = []
    with path.open(newline='', encoding='utf-8', errors='ignore') as f:
        for row in csv.DictReader(f):
            if row.get('company') or row.get('legal_name'):
                ids.append(upsert_candidate(row, path, dedupe_key, opp_folder))
    return ids


def scan_all() -> dict:
    migrate()
    files = sorted(OPPS.glob('*/scope_sheets/subcontractor_candidates.csv'))
    out = {'scanned_files': len(files), 'imported_ids': [], 'files': []}
    for p in files:
        before = len(out['imported_ids'])
        ids = import_csv(p)
        out['imported_ids'].extend(ids)
        out['files'].append({'path': str(p), 'rows': len(ids), 'new_total_ids_seen': len(out['imported_ids']) - before})
    write_reports()
    out['unique_subcontractors_total'] = count_subs()
    return out


def count_subs() -> int:
    c = con(); n = c.execute('select count(*) from subcontractors').fetchone()[0]; c.close(); return int(n)


def search(trade: str = '', state: str = '', county: str = '', city: str = '', limit: int = 50) -> list[dict]:
    migrate(); c = con()
    sql = '''select distinct s.id, s.legal_name, s.website, s.notes, s.validation_date,
                    group_concat(distinct t.trade) trades,
                    group_concat(distinct t.naics) naics,
                    group_concat(distinct g.city || ', ' || g.state) service_points,
                    group_concat(distinct cc.phone) phones,
                    group_concat(distinct cc.email) emails
             from subcontractors s
             left join subcontractor_trades t on t.subcontractor_id=s.id
             left join subcontractor_geography g on g.subcontractor_id=s.id
             left join subcontractor_contacts cc on cc.subcontractor_id=s.id
             where 1=1'''
    params = []
    if trade:
        sql += ' and (lower(t.trade) like ? or lower(t.naics) like ?)'; params += [f'%{trade.lower()}%', f'%{trade.lower()}%']
    if state:
        sql += ' and lower(g.state)=lower(?)'; params.append(state)
    if county:
        sql += ' and lower(g.county) like lower(?)'; params.append(f'%{county}%')
    if city:
        sql += ' and lower(g.city) like lower(?)'; params.append(f'%{city}%')
    sql += ' group by s.id order by s.validation_date desc, s.legal_name limit ?'; params.append(limit)
    rows = [dict(r) for r in c.execute(sql, params)]
    c.execute('insert or replace into subcontractor_search_cache(query_trade,query_state,query_county,query_city,result_json,searched_at) values(?,?,?,?,?,?)', (trade, state, county, city, json.dumps(rows), now()))
    c.commit(); c.close(); return rows


def write_reports() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    c = con()
    rows = [dict(r) for r in c.execute('''select s.id, s.legal_name, s.website, s.validation_date, s.source,
         group_concat(distinct t.trade) trades, group_concat(distinct t.naics) naics,
         group_concat(distinct g.city || ', ' || g.state) service_points,
         group_concat(distinct cc.phone) phones, group_concat(distinct cc.email) emails
       from subcontractors s
       left join subcontractor_trades t on t.subcontractor_id=s.id
       left join subcontractor_geography g on g.subcontractor_id=s.id
       left join subcontractor_contacts cc on cc.subcontractor_id=s.id
       group by s.id order by s.legal_name''')]
    c.close()
    csv_path = REPORTS / 'subcontractor_master.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        fields = ['id','legal_name','website','validation_date','source','trades','naics','service_points','phones','emails']
        w = csv.DictWriter(f, fields); w.writeheader(); w.writerows(rows)
    md = ['# WFG Subcontractor Master CRM', '', f'Updated: {now()}', f'Total subcontractors: {len(rows)}', '', '## Records', '']
    for r in rows:
        md += [f'### {r["legal_name"]}', f'- ID: {r["id"]}', f'- Website: {r.get("website") or "UNKNOWN"}', f'- Trades/NAICS: {r.get("trades") or "UNKNOWN"} / {r.get("naics") or "UNKNOWN"}', f'- Service points: {r.get("service_points") or "UNKNOWN"}', f'- Phones: {r.get("phones") or "UNKNOWN"}', f'- Emails: {r.get("emails") or "UNKNOWN"}', '']
    (REPORTS / 'subcontractor_master.md').write_text('\n'.join(md), encoding='utf-8')


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('migrate')
    sub.add_parser('scan-all')
    a = sub.add_parser('import-csv'); a.add_argument('path')
    a = sub.add_parser('search'); a.add_argument('--trade', default=''); a.add_argument('--state', default=''); a.add_argument('--county', default=''); a.add_argument('--city', default=''); a.add_argument('--limit', type=int, default=50)
    sub.add_parser('report')
    args = p.parse_args()
    if args.cmd == 'migrate': migrate(); print(json.dumps({'ok': True, 'db': str(DB)}, indent=2)); return 0
    if args.cmd == 'scan-all': print(json.dumps(scan_all(), indent=2)); return 0
    if args.cmd == 'import-csv': print(json.dumps({'ids': import_csv(Path(args.path))}, indent=2)); write_reports(); return 0
    if args.cmd == 'search': print(json.dumps(search(args.trade, args.state, args.county, args.city, args.limit), indent=2)); return 0
    if args.cmd == 'report': migrate(); write_reports(); print(json.dumps({'ok': True, 'reports': [str(REPORTS/'subcontractor_master.csv'), str(REPORTS/'subcontractor_master.md')]}, indent=2)); return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
