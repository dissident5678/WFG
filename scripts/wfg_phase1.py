#!/usr/bin/env python3
"""Phase 1 durable batch/state support for the WFG SAM.gov morning pipeline."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/gov-contracting')).resolve()
ARCHIVE = Path(os.environ.get('WFG_ARCHIVE_DIR', str(PROJECT / 'opportunity-searches' / 'sam-api'))).resolve()
BATCHES = Path(os.environ.get('WFG_BATCHES_DIR', str(ARCHIVE / 'batches'))).resolve()
STATE_DIR = Path(os.environ.get('WFG_STATE_DIR', str(PROJECT / 'state'))).resolve()
LOG_DIR = Path(os.environ.get('WFG_LOG_DIR', str(PROJECT / 'logs' / 'sam-pipeline'))).resolve()
DB_PATH = Path(os.environ.get('WFG_DB_PATH', str(STATE_DIR / 'wfg_workflow.sqlite3'))).resolve()
CURRENT_BATCH = ARCHIVE / 'current-batch.txt'
LAST_SUCCESSFUL_BATCH = ARCHIVE / 'last-successful-batch.txt'
LOCK_PATH = STATE_DIR / 'sam_pipeline.lock'


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def code_version() -> str:
    h = hashlib.sha256()
    for p in [PROJECT / 'scripts' / 'sam_morning_opportunity_brief.py', PROJECT / 'scripts' / 'sync_sam_opportunity_tracker.py', PROJECT / 'scripts' / 'wfg_phase1.py']:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


@contextlib.contextmanager
def pipeline_lock(stage: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, 'w') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'pipeline lock is held; {stage} refused to overlap')
        f.write(f'{stage} {os.getpid()} {utc_now()}\n'); f.flush()
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_manifest(batch_dir: Path) -> dict[str, Any]:
    mp = batch_dir / 'manifest.json'
    if not mp.exists():
        return {}
    return json.loads(mp.read_text())


def write_manifest(batch_dir: Path, manifest: dict[str, Any]) -> None:
    manifest['updated_at'] = utc_now()
    atomic_write(batch_dir / 'manifest.json', json.dumps(manifest, indent=2, sort_keys=True))


def append_log(stage: str, batch_id: str | None, level: str, message: str, **fields: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {'ts': utc_now(), 'stage': stage, 'batch_id': batch_id, 'level': level, 'message': message}
    rec.update({k: v for k, v in fields.items() if v is not None})
    with (LOG_DIR / f'{dt.date.today().isoformat()}.jsonl').open('a') as f:
        f.write(json.dumps(rec, sort_keys=True) + '\n')


def new_batch_id() -> str:
    return 'sam-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')


def create_batch() -> tuple[str, Path, dict[str, Any]]:
    batch_id = new_batch_id()
    bdir = BATCHES / batch_id
    bdir.mkdir(parents=True, exist_ok=False)
    manifest = {
        'batch_id': batch_id,
        'created_at': utc_now(),
        'snapshot_status': 'pending', 'snapshot_file': None,
        'fetch_status': 'pending', 'fetch_started_at': None, 'fetch_completed_at': None,
        'raw_files': [], 'api_pages': 0, 'records': 0,
        'enrichment_status': 'pending', 'brief_status': 'pending', 'tracker_sync_status': 'pending',
        'warnings': [], 'errors': [], 'code_config_version': code_version(),
    }
    write_manifest(bdir, manifest)
    atomic_write(CURRENT_BATCH, batch_id + '\n')
    init_db()
    db_execute('insert or replace into batches(batch_id, created_at, status, manifest_path, code_version) values(?,?,?,?,?)',
               (batch_id, manifest['created_at'], 'created', str(bdir / 'manifest.json'), manifest['code_config_version']))
    append_log('batch', batch_id, 'info', 'created batch')
    return batch_id, bdir, manifest


def current_batch_dir(require: bool = True) -> tuple[str | None, Path | None]:
    if not CURRENT_BATCH.exists():
        if require: raise RuntimeError('no current batch id file')
        return None, None
    bid = CURRENT_BATCH.read_text().strip()
    if not bid:
        if require: raise RuntimeError('empty current batch id file')
        return None, None
    return bid, BATCHES / bid


def current_completed_batch_dir() -> tuple[str, Path, dict[str, Any]]:
    bid, bdir = current_batch_dir(True)
    assert bid and bdir
    m = read_manifest(bdir)
    if m.get('fetch_status') != 'completed':
        raise RuntimeError(f'current batch {bid} fetch_status={m.get("fetch_status")} is not completed; stale brief prevented')
    return bid, bdir, m


def mark(batch_dir: Path, **updates: Any) -> dict[str, Any]:
    m = read_manifest(batch_dir)
    for k, v in updates.items():
        if k in ('warnings', 'errors'):
            m.setdefault(k, []).extend(v if isinstance(v, list) else [v])
        else:
            m[k] = v
    write_manifest(batch_dir, m)
    status = m.get('fetch_status') or m.get('snapshot_status') or 'updated'
    if m.get('batch_id'):
        db_execute('update batches set status=?, completed_at=case when ? in ("completed","failed") then ? else completed_at end, record_count=?, raw_file_count=? where batch_id=?',
                   (status, status, utc_now(), int(m.get('records') or 0), len(m.get('raw_files') or []), m.get('batch_id')))
    return m


def batch_raw_path(batch_dir: Path, page_number: int) -> Path:
    return batch_dir / f'raw-p{page_number}.json'


def copy_batch_raw_to_archive(batch_dir: Path, raw_path: Path, page_number: int) -> Path:
    bid = read_manifest(batch_dir).get('batch_id') or batch_dir.name
    legacy = ARCHIVE / f'raw-{bid.replace("sam-", "")}-p{page_number}.json'
    legacy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_path, legacy)
    return legacy


def load_items_from_batch(batch_dir: Path) -> list[dict[str, Any]]:
    m = read_manifest(batch_dir)
    items: list[dict[str, Any]] = []
    for rel in m.get('raw_files') or []:
        p = batch_dir / rel if not str(rel).startswith('/') else Path(rel)
        data = json.loads(p.read_text(errors='replace'))
        page = data.get('opportunitiesData') or data.get('data') or []
        items.extend(x for x in page if isinstance(x, dict))
    return items


def item_hash(item: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def init_db(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, manifest_path TEXT, code_version TEXT, record_count INTEGER DEFAULT 0, raw_file_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS opportunities(dedupe_key TEXT PRIMARY KEY, notice_id TEXT, solicitation_number TEXT, title TEXT, agency TEXT, naics TEXT, set_aside TEXT, notice_type TEXT, place_of_performance TEXT, response_deadline TEXT, first_seen TEXT, last_seen TEXT, latest_version_hash TEXT, score INTEGER, bucket TEXT, workflow_status TEXT DEFAULT 'discovered', source_batch TEXT, sam_link TEXT);
        CREATE TABLE IF NOT EXISTS opportunity_versions(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT NOT NULL, version_hash TEXT NOT NULL, source_batch TEXT, seen_at TEXT NOT NULL, raw_json TEXT NOT NULL, UNIQUE(dedupe_key, version_hash));
        CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, resource_url TEXT NOT NULL, resource_name TEXT, content_hash TEXT DEFAULT '', version_hash TEXT, first_seen TEXT, last_seen TEXT, source_batch TEXT, UNIQUE(dedupe_key, resource_url, content_hash));
        CREATE TABLE IF NOT EXISTS workflow_events(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, event_type TEXT NOT NULL, event_at TEXT NOT NULL, actor TEXT, source_batch TEXT, details_json TEXT);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT, gate TEXT NOT NULL, requested_at TEXT, decided_at TEXT, decision TEXT, approver TEXT, record_path TEXT, details_json TEXT);
        CREATE TABLE IF NOT EXISTS processing_errors(id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, stage TEXT NOT NULL, error_at TEXT NOT NULL, error_type TEXT, message TEXT, details_json TEXT);
        CREATE TRIGGER IF NOT EXISTS workflow_events_no_update BEFORE UPDATE ON workflow_events BEGIN SELECT RAISE(ABORT, 'workflow_events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete BEFORE DELETE ON workflow_events BEGIN SELECT RAISE(ABORT, 'workflow_events are append-only'); END;
        ''')
        # Phase 4 environment/test separation columns. ALTERs are idempotent via duplicate-column ignore.
        for table, coldef in [
            ('batches', 'environment TEXT DEFAULT \"production\"'),
            ('opportunities', 'environment TEXT DEFAULT \"production\"'),
            ('opportunities', 'is_test_fixture INTEGER DEFAULT 0'),
            ('opportunity_versions', 'environment TEXT DEFAULT \"production\"'),
            ('attachments', 'reference_status TEXT DEFAULT \"discovered_reference\"'),
            ('attachments', 'download_status TEXT DEFAULT \"not_attempted\"'),
            ('attachments', 'local_path TEXT'),
            ('attachments', 'parse_status TEXT DEFAULT \"not_parsed\"'),
            ('attachments', 'environment TEXT DEFAULT \"production\"'),
            ('workflow_events', 'environment TEXT DEFAULT \"production\"'),
            ('approvals', 'approval_id TEXT'),
            ('approvals', 'exact_action TEXT'),
            ('approvals', 'expires_at TEXT'),
            ('approvals', 'used_at TEXT'),
            ('approvals', 'superseded_by TEXT'),
            ('approvals', 'environment TEXT DEFAULT \"production\"'),
            ('processing_errors', 'environment TEXT DEFAULT \"production\"'),
        ]:
            try:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {coldef}')
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e).lower(): raise
        con.execute('CREATE INDEX IF NOT EXISTS idx_approvals_approval_id ON approvals(approval_id)')
        con.commit()
    finally:
        con.close()


def db_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    init_db()
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(sql, params); con.commit()
    finally:
        con.close()


def record_error(batch_id: str | None, stage: str, exc: BaseException | str, details: dict[str, Any] | None = None) -> None:
    msg = str(exc)
    db_execute('insert into processing_errors(batch_id, stage, error_at, error_type, message, details_json) values(?,?,?,?,?,?)',
               (batch_id, stage, utc_now(), type(exc).__name__ if not isinstance(exc, str) else 'Error', msg[:2000], json.dumps(details or {})))
    append_log(stage, batch_id, 'error', msg[:1000])


def upsert_opportunity(item: dict[str, Any], dedupe_key: str, source_batch: str, bucket: str | None = None, score: int | None = None) -> None:
    init_db()
    vh = item_hash(item); now = utc_now()
    pop = item.get('placeOfPerformance')
    pop_s = json.dumps(pop, sort_keys=True) if isinstance(pop, dict) else str(pop or '')
    vals = (dedupe_key, item.get('noticeId'), item.get('solicitationNumber'), item.get('title'), item.get('fullParentPathName') or item.get('department'), ','.join([str(x) for x in ([item.get('naicsCode')] if item.get('naicsCode') else [])]), item.get('typeOfSetAsideDescription') or item.get('typeOfSetAside'), item.get('type'), pop_s, item.get('responseDeadLine') or item.get('responseDeadline'), now, now, vh, score, bucket, source_batch, item.get('uiLink'))
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute('''insert into opportunities(dedupe_key,notice_id,solicitation_number,title,agency,naics,set_aside,notice_type,place_of_performance,response_deadline,first_seen,last_seen,latest_version_hash,score,bucket,source_batch,sam_link)
                       values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       on conflict(dedupe_key) do update set title=excluded.title, agency=excluded.agency, naics=excluded.naics, set_aside=excluded.set_aside, notice_type=excluded.notice_type, place_of_performance=excluded.place_of_performance, response_deadline=excluded.response_deadline, last_seen=excluded.last_seen, latest_version_hash=excluded.latest_version_hash, score=coalesce(excluded.score, opportunities.score), bucket=coalesce(excluded.bucket, opportunities.bucket), source_batch=excluded.source_batch, sam_link=excluded.sam_link''', vals)
        con.execute('insert or ignore into opportunity_versions(dedupe_key, version_hash, source_batch, seen_at, raw_json) values(?,?,?,?,?)', (dedupe_key, vh, source_batch, now, json.dumps(item, ensure_ascii=False, sort_keys=True)))
        for link in item.get('resourceLinks') or []:
            if isinstance(link, dict):
                url = str(link.get('href') or link.get('url') or link.get('link') or '')
                name = str(link.get('rel') or link.get('title') or link.get('name') or '')
            else:
                url, name = str(link), ''
            if url:
                con.execute('insert or ignore into attachments(dedupe_key,resource_url,resource_name,first_seen,last_seen,source_batch) values(?,?,?,?,?,?)', (dedupe_key, url, name, now, now, source_batch))
        con.execute('insert into workflow_events(dedupe_key,event_type,event_at,actor,source_batch,details_json) values(?,?,?,?,?,?)', (dedupe_key, 'discovered_or_seen', now, 'sam_pipeline', source_batch, json.dumps({'version_hash': vh})))
        # Phase 4 environment/test separation columns. ALTERs are idempotent via duplicate-column ignore.
        for table, coldef in [
            ('batches', 'environment TEXT DEFAULT \"production\"'),
            ('opportunities', 'environment TEXT DEFAULT \"production\"'),
            ('opportunities', 'is_test_fixture INTEGER DEFAULT 0'),
            ('opportunity_versions', 'environment TEXT DEFAULT \"production\"'),
            ('attachments', 'reference_status TEXT DEFAULT \"discovered_reference\"'),
            ('attachments', 'download_status TEXT DEFAULT \"not_attempted\"'),
            ('attachments', 'local_path TEXT'),
            ('attachments', 'parse_status TEXT DEFAULT \"not_parsed\"'),
            ('attachments', 'environment TEXT DEFAULT \"production\"'),
            ('workflow_events', 'environment TEXT DEFAULT \"production\"'),
            ('approvals', 'approval_id TEXT'),
            ('approvals', 'exact_action TEXT'),
            ('approvals', 'expires_at TEXT'),
            ('approvals', 'used_at TEXT'),
            ('approvals', 'superseded_by TEXT'),
            ('approvals', 'environment TEXT DEFAULT \"production\"'),
            ('processing_errors', 'environment TEXT DEFAULT \"production\"'),
        ]:
            try:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {coldef}')
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e).lower(): raise
        con.execute('CREATE INDEX IF NOT EXISTS idx_approvals_approval_id ON approvals(approval_id)')
        con.commit()
    finally:
        con.close()


def short_failure(stage: str, batch_id: str | None, err: str, stale_prevented: bool = True) -> str:
    return (f'WFG SAM.gov pipeline error\n'
            f'Batch: {batch_id or "UNKNOWN"}\n'
            f'Stage: {stage}\n'
            f'Error: {err[:500]}\n'
            f'Stale data prevented: {"yes" if stale_prevented else "no"}\n'
            f'Recommended action: inspect {LOG_DIR} and the batch manifest, then rerun the failed stage after fixing the cause.')
