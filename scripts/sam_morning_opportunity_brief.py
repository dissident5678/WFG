#!/usr/bin/env python3
"""Morning SAM.gov opportunity briefing for Wright Foster Group LLC (v2).

Key changes vs v1:
- Paginates the SAM.gov search (limit=1000 per request) so the full daily
  posting volume is screened instead of the first 100 records.
- Gates candidates on a configurable NAICS/PSC/keyword target profile
  (opportunity-searches/sam_search_profile.json) so DLA spare-parts noise
  never reaches scoring.
- Word-boundary risk keywords (v1 flagged "CIRCUIT BREAKER" as CUI).
- Optionally fetches real description text for top finalists (the search
  API returns a URL in the description field, not text) so dollar
  magnitude and scope can actually be screened.
- Buckets output: PURSUE / SOURCES SOUGHT / WATCH instead of one list.
- Keeps deadline-urgent small RFQs (7-14 days) instead of dropping them.

Reads SAM_GOV_API_KEY from environment or /home/nick/.hermes/.env.
Prints a Telegram-ready briefing. Designed for Hermes cron no_agent delivery.

Offline testing: --offline scores archived raw-*.json files without
calling the API. --no-sync skips the Google Sheet tracker subprocess.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import wfg_phase1

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/gov-contracting'))
ARCHIVE = PROJECT / 'opportunity-searches' / 'sam-api'
PROFILE_PATH = PROJECT / 'opportunity-searches' / 'sam_search_profile.json'
DESC_CACHE_PATH = ARCHIVE / 'desc-cache.json'
SNAPSHOT_DIR = ARCHIVE / 'snapshots'
TRACKER_SYNC_SCRIPT = PROJECT / 'scripts' / 'sync_sam_opportunity_tracker.py'
ENV_PATHS = [Path('/home/nick/.hermes/.env'), PROJECT / '.env']
API_URL = 'https://api.sam.gov/opportunities/v2/search'
CRON_SOFT_DEADLINE_SECONDS = int(os.environ.get('WFG_SAM_BRIEF_SOFT_DEADLINE_SECONDS', '105'))


def seconds_left(start: float) -> float:
    """Remaining wall-clock budget before Hermes cron's 120s no_agent timeout.

    no_agent cron kills the entire script at ~120s, which means even already
    printed stdout is lost. Keep all network/subprocess work under a soft
    deadline so the script can still deliver a useful briefing plus warnings.
    """
    return CRON_SOFT_DEADLINE_SECONDS - (time.monotonic() - start)

DEFAULT_PROFILE: dict = {
    'api': {'posted_lookback_days': 3, 'limit_per_request': 1000,
            'max_search_requests_per_run': 5, 'fetch_descriptions_for_top': 4,
            'ptype': 'o,k,r,p'},
    'value_band': {'ideal_min': 10_000, 'ideal_max': 250_000, 'hard_max': 500_000,
                   'construction_bond_flag_threshold': 150_000},
    'deadlines': {'drop_below_days': 7, 'urgent_below_days': 14},
    'naics_core': {}, 'naics_watch': {},
    'psc_prefixes_core': ['S2', 'Z1', 'Z2'], 'psc_prefixes_watch': ['J0', 'Z3'],
    'title_keywords_strong': [], 'title_keywords_weak': [],
    'risk_keywords': [], 'bond_keywords': [], 'wage_keywords': [],
    'set_asides': {'bid_ok_codes': ['SBA', 'SBP'], 'watch_only_codes': []},
    'region_boost_states': ['MD', 'VA', 'DC', 'PA', 'DE', 'WV'],
    'brief': {'max_pursue': 10, 'max_urgent': 5, 'max_sources_sought': 5, 'max_watch': 10,
              'min_score_pursue': 30},
}


def load_profile() -> dict:
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    if PROFILE_PATH.exists():
        try:
            user = json.loads(PROFILE_PATH.read_text())
        except Exception:
            return profile
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(profile.get(k), dict):
                profile[k].update(v)
            else:
                profile[k] = v
    return profile


def load_dotenv() -> None:
    for p in ENV_PATHS:
        if not p.exists():
            continue
        for line in p.read_text(errors='ignore').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def get_key() -> str | None:
    load_dotenv()
    for k in ('SAM_GOV_API_KEY', 'SAM_API_KEY', 'SAMGOV_API_KEY'):
        v = os.environ.get(k, '').strip()
        if v:
            return v
    return None


def money_to_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).replace(',', '')
    m = re.search(r'\$?\s*(\d+(?:\.\d+)?)\s*([kKmM]?)', s)
    if not m:
        return None
    n = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == 'k':
        n *= 1_000
    elif suffix == 'm':
        n *= 1_000_000
    return int(n)


def scope_text(item: dict, descriptions: dict[str, str]) -> str:
    """Real scope text: title plus fetched description if available.

    The v2 search API returns a URL in item['description'], not text, so it
    must never be scanned for keywords or dollar values.
    """
    parts = [str(item.get('title') or '')]
    desc = descriptions.get(str(item.get('noticeId') or ''))
    if desc:
        parts.append(desc)
    return ' '.join(parts).lower()


def estimated_value(item: dict, descriptions: dict[str, str]) -> int | None:
    candidates = []
    for key in ('estimatedValue', 'estimated_value', 'baseAndAllOptionsValue', 'magnitude'):
        if item.get(key):
            candidates.append(item.get(key))
    award = item.get('award')
    if isinstance(award, dict) and award.get('amount'):
        candidates.append(award.get('amount'))
    text = scope_text(item, descriptions)
    for m in re.finditer(r'\$\s*[\d,]+(?:\.\d+)?\s*(?:million|[km])?', text, re.I):
        candidates.append(m.group(0))
    for m in re.finditer(r'(?:between|under|less than|not (?:to )?exceed|magnitude)[^.$]{0,60}\$?\s*[\d,]{4,}(?:\.\d+)?\s*(?:million|[km])?', text, re.I):
        candidates.append(m.group(0))
    nums = [money_to_int(x) for x in candidates]
    nums = [n for n in nums if n is not None and n >= 1000]
    return max(nums) if nums else None


def keyword_hits(words: list[str], text: str) -> list[str]:
    hits = []
    for w in words:
        if re.search(r'(?<!\w)' + re.escape(w.lower()) + r'(?!\w)', text):
            hits.append(w)
    return hits


def deadline_days(item: dict) -> int | None:
    raw = item.get('responseDeadLine') or item.get('responseDeadline') or item.get('response_date')
    if not raw:
        return None
    s = str(raw).strip().replace('Z', '+00:00')
    d = None
    try:
        d = dt.datetime.fromisoformat(s)
    except Exception:
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
    return (d.date() - dt.date.today()).days


def pop_state(item: dict) -> str:
    pop = item.get('placeOfPerformance')
    if isinstance(pop, dict):
        st = pop.get('state')
        if isinstance(st, dict):
            return str(st.get('code') or '').upper()
        return str(st or '').upper()
    return ''


def pop_label(item: dict) -> str:
    pop = item.get('placeOfPerformance')
    if isinstance(pop, dict):
        city = pop.get('city')
        city = city.get('name') if isinstance(city, dict) else city
        st = pop_state(item)
        return ', '.join(x for x in [str(city or '').strip(), st] if x)
    return str(pop or '').strip()


def item_naics(item: dict) -> list[str]:
    codes = []
    if item.get('naicsCode'):
        codes.append(str(item['naicsCode']))
    raw = item.get('naicsCodes')
    if isinstance(raw, list):
        codes.extend(str(c) for c in raw)
    return list(dict.fromkeys(codes))


def pop_country(item: dict) -> str:
    pop = item.get('placeOfPerformance')
    if isinstance(pop, dict):
        c = pop.get('country')
        if isinstance(c, dict):
            return str(c.get('code') or '').upper()
        return str(c or '').upper()
    return ''


def set_aside_bucket(item: dict, profile: dict) -> str:
    """Returns 'ok', 'watch', or 'open'."""
    code = str(item.get('typeOfSetAside') or '').strip()
    desc = str(item.get('typeOfSetAsideDescription') or '').lower()
    sa = profile['set_asides']
    if code in sa.get('watch_only_codes', []):
        return 'watch'
    if any(x in desc for x in ['women', 'wosb', 'veteran', 'sdvosb', '8a', '8(a)', 'hubzone', 'indian', 'isbee']):
        return 'watch'
    if code in sa.get('bid_ok_codes', []) or 'total small business' in desc or 'partial small business' in desc:
        return 'ok'
    return 'open'



def apparent_contract_type(item: dict, descriptions: dict[str, str]) -> str:
    text = scope_text(item, descriptions)
    codes = item_naics(item)
    if any(c.startswith(('236', '237', '238')) for c in codes):
        return 'construction/special trade'
    if any(c.startswith(('56', '81')) for c in codes):
        return 'service'
    if 'supply' in text or 'deliver' in text:
        return 'supply or delivery'
    return 'unknown'

def classify(item: dict, profile: dict, descriptions: dict[str, str]) -> tuple[str, int, list[str], list[str]]:
    """Returns (bucket, score, reasons, watchouts).

    bucket: 'pursue' | 'sources_sought' | 'watch' | 'reject'
    """
    reasons: list[str] = []
    watch: list[str] = []
    score = 0
    text = scope_text(item, descriptions)
    title = str(item.get('title') or '').lower()
    naics = item_naics(item)
    psc = str(item.get('classificationCode') or '')
    vb = profile['value_band']
    dl = profile['deadlines']

    # --- domestic gate: overseas embassy/base work is not winnable with a local-sub model ---
    country = pop_country(item)
    if profile.get('domestic_only', True) and country and country not in ('USA', 'US', 'UNITED STATES'):
        return 'reject', 0, [], [f'overseas place of performance ({country})']

    # --- target gate: must look like WFG's trades at all ---
    core_naics = [c for c in naics if c in profile['naics_core']]
    watch_naics = [c for c in naics if c in profile['naics_watch']]
    core_psc = any(psc.startswith(p) for p in profile['psc_prefixes_core'])
    watch_psc = any(psc.startswith(p) for p in profile['psc_prefixes_watch'])
    strong_kw = keyword_hits(profile['title_keywords_strong'], title)
    if not (core_naics or watch_naics or core_psc or watch_psc or strong_kw):
        return 'reject', 0, [], ['outside target trades (NAICS/PSC/keywords)']

    if core_naics:
        score += 35
        reasons.append('core trade NAICS ' + ', '.join(f'{c} ({profile["naics_core"][c]})' for c in core_naics[:2]))
    elif watch_naics:
        score += 12
        watch.append('watchlist NAICS ' + ', '.join(f'{c} ({profile["naics_watch"][c]})' for c in watch_naics[:2]))
    if core_psc:
        score += 12
        reasons.append(f'target PSC {psc}')
    elif watch_psc:
        score += 5
    if strong_kw:
        score += min(12, 4 * len(strong_kw))
        reasons.append('scope keywords: ' + ', '.join(strong_kw[:4]))
    elif keyword_hits(profile['title_keywords_weak'], title) and (core_naics or core_psc):
        score += 4

    # --- deadline ---
    days = deadline_days(item)
    if days is not None:
        if days < dl['drop_below_days']:
            return 'reject', 0, [], [f'deadline too close: {days} days']
        if days < dl['urgent_below_days']:
            score += 5
            watch.append(f'URGENT: due in {days} days — needs same-day intake and sub quotes')
        elif days <= 30:
            score += 12
            reasons.append(f'workable deadline: {days} days')
        else:
            score += 6
            reasons.append(f'comfortable deadline: {days} days')
    else:
        watch.append('deadline unclear — verify on SAM')

    # --- value screen ---
    ev = estimated_value(item, descriptions)
    if ev is not None:
        if ev > vb['hard_max']:
            return 'reject', 0, [], [f'visible value ~${ev:,.0f} exceeds ${vb["hard_max"]:,.0f} cap']
        if ev <= vb['ideal_max']:
            score += 12
            reasons.append(f'starter-size value ~${ev:,.0f}')
        else:
            score += 3
            watch.append(f'value ~${ev:,.0f} above ideal ${vb["ideal_max"]:,.0f} band')
        if ev >= vb['construction_bond_flag_threshold'] and any(c.startswith(('236', '237', '238')) for c in naics):
            watch.append('construction at/above $150K — Miller Act performance/payment bonds likely')
    else:
        watch.append('no visible value — confirm magnitude before bid/no-bid')

    # --- set-aside ---
    sab = set_aside_bucket(item, profile)
    if sab == 'watch':
        watch.append('certification-gated set-aside (' + str(item.get('typeOfSetAsideDescription') or item.get('typeOfSetAside')) + ') — WFG not certified yet')
        return 'watch', score, reasons, watch
    if sab == 'ok':
        score += 10
        reasons.append('small-business set-aside')
    else:
        score += 4

    # --- region ---
    st = pop_state(item)
    if st in profile['region_boost_states']:
        score += 10
        reasons.append(f'home region ({st})')

    # --- risk / compliance flags ---
    risk = keyword_hits(profile['risk_keywords'], text)
    if risk:
        score -= 15 * len(risk)
        watch.append('risk terms: ' + ', '.join(risk[:4]))
    bonds = keyword_hits(profile['bond_keywords'], text)
    if bonds:
        score -= 8
        watch.append('bonding referenced: ' + ', '.join(bonds[:3]))
    wages = keyword_hits(profile['wage_keywords'], text)
    if wages:
        watch.append('SCA/DBA wage determination applies — price labor from the WD rates')

    # --- notice type buckets ---
    ntype = str(item.get('type') or '').lower()
    if 'sources sought' in ntype:
        return 'sources_sought', score, reasons, watch
    if 'presolicitation' in ntype:
        return 'watch', score, reasons, watch
    score += 6  # solicitation / combined synopsis: actionable now
    return 'pursue', score, reasons, watch


def fetch_paginated(api_key: str, profile: dict, start: float, batch_dir: Path | None = None) -> tuple[list[dict], int, int, list[str]]:
    """Returns (items, total_records_reported, requests_used)."""
    api = profile['api']
    today = dt.date.today()
    posted_from = today - dt.timedelta(days=int(api['posted_lookback_days']))
    base = {
        'api_key': api_key,
        'postedFrom': posted_from.strftime('%m/%d/%Y'),
        'postedTo': today.strftime('%m/%d/%Y'),
        'limit': str(int(api['limit_per_request'])),
        'ptype': api['ptype'],
    }
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    items: list[dict] = []
    total = 0
    requests_used = 0
    warnings: list[str] = []
    offset = 0
    while requests_used < int(api['max_search_requests_per_run']):
        if seconds_left(start) < 20:
            warnings.append('Stopped SAM.gov pagination early to avoid cron timeout; results may be partial.')
            break
        params = dict(base, offset=str(offset))
        url = API_URL + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'WFG-Hermes-SAM-Brief/2.0'})
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                request_timeout = max(8, min(25, int(seconds_left(start) - 10)))
                with urllib.request.urlopen(req, timeout=request_timeout) as r:
                    raw = r.read().decode('utf-8', 'replace')
                break
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code not in (500, 502, 503, 504) or attempt == 3:
                    raise
                time.sleep(min(5 * attempt, max(0, seconds_left(start) - 10)))
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                if attempt == 3:
                    raise
                time.sleep(min(5 * attempt, max(0, seconds_left(start) - 10)))
        else:
            raise RuntimeError(f'SAM.gov request failed after retries: {last_error}')
        requests_used += 1
        if batch_dir is not None:
            raw_path = wfg_phase1.batch_raw_path(batch_dir, requests_used)
            raw_path.write_text(raw)
            legacy = wfg_phase1.copy_batch_raw_to_archive(batch_dir, raw_path, requests_used)
            manifest = wfg_phase1.read_manifest(batch_dir)
            manifest.setdefault('raw_files', []).append(raw_path.name)
            manifest.setdefault('legacy_raw_files', []).append(str(legacy))
            manifest['api_pages'] = requests_used
            wfg_phase1.write_manifest(batch_dir, manifest)
        else:
            (ARCHIVE / f'raw-{stamp}-p{requests_used}.json').write_text(raw)
        data = json.loads(raw)
        page = data.get('opportunitiesData') or data.get('data') or []
        total = int(data.get('totalRecords') or 0)
        items.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return items, total, requests_used, warnings


def load_desc_cache() -> dict[str, str]:
    if DESC_CACHE_PATH.exists():
        try:
            return json.loads(DESC_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_desc_cache(cache: dict[str, str]) -> None:
    try:
        DESC_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # keep the cache bounded
        if len(cache) > 400:
            cache = dict(list(cache.items())[-400:])
        DESC_CACHE_PATH.write_text(json.dumps(cache))
    except Exception:
        pass


def fetch_description(item: dict, api_key: str, cache: dict[str, str], start: float) -> bool:
    """Fetch real description text for one notice. Returns True if an API request was spent."""
    nid = str(item.get('noticeId') or '')
    if not nid or nid in cache:
        return False
    url = str(item.get('description') or '')
    if not url.startswith('http'):
        return False
    if seconds_left(start) < 12:
        return False
    sep = '&' if '?' in url else '?'
    req = urllib.request.Request(url + sep + 'api_key=' + urllib.parse.quote(api_key),
                                 headers={'User-Agent': 'WFG-Hermes-SAM-Brief/2.0'})
    try:
        request_timeout = max(5, min(15, int(seconds_left(start) - 5)))
        with urllib.request.urlopen(req, timeout=request_timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
        try:
            text = str(json.loads(raw).get('description') or '')
        except Exception:
            text = raw
        text = re.sub(r'<[^>]+>', ' ', text)
        cache[nid] = re.sub(r'\s+', ' ', text).strip()[:8000]
    except Exception:
        cache[nid] = ''
    return True


def item_link(item: dict) -> str:
    return str(item.get('uiLink') or item.get('url') or item.get('link') or '').strip()


def dedupe_key(item: dict) -> str:
    """Match the Google Sheet tracker dedupe key."""
    for key in ('noticeId', 'notice_id'):
        if item.get(key):
            return 'notice:' + str(item[key]).strip().lower()
    for key in ('solicitationNumber', 'solicitation_number'):
        if item.get(key):
            return 'sol:' + str(item[key]).strip().lower()
    return 'fallback:' + '|'.join(str(item.get(k, '')).strip().lower() for k in ('title', 'postedDate', 'uiLink'))


def load_seen_tracker_keys() -> tuple[set[str], str | None]:
    if not TRACKER_SYNC_SCRIPT.exists():
        return set(), f'tracker sync script missing: {TRACKER_SYNC_SCRIPT}'
    try:
        res = subprocess.run(
            [sys.executable, str(TRACKER_SYNC_SCRIPT), '--print-seen-keys'],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except Exception as e:
        return set(), f'could not read opportunity tracker: {type(e).__name__}: {e}'
    if res.returncode != 0:
        return set(), 'could not read opportunity tracker: ' + (res.stderr.strip() or res.stdout.strip())[:300]
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}, None


def load_seen_keys_file(path: Path) -> tuple[set[str], str | None]:
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return set(), f'could not read seen-key snapshot {path}: {type(e).__name__}: {e}'
    keys = data.get('seen_keys', []) if isinstance(data, dict) else data
    if not isinstance(keys, list):
        return set(), f'seen-key snapshot {path} has invalid format'
    return {str(k).strip() for k in keys if str(k).strip()}, None


def latest_seen_keys_file() -> Path | None:
    files = sorted(SNAPSHOT_DIR.glob('seen-keys-*.json'))
    return files[-1] if files else None


def sync_tracker_after_run(start: float) -> str | None:
    if not TRACKER_SYNC_SCRIPT.exists():
        return f'tracker sync script missing: {TRACKER_SYNC_SCRIPT}'
    remaining = seconds_left(start)
    if remaining < 20:
        return 'skipped final tracker sync to avoid cron timeout; next run will re-sync archived raw results.'
    try:
        timeout = max(10, min(45, int(remaining - 5)))
        res = subprocess.run(
            [sys.executable, str(TRACKER_SYNC_SCRIPT), '--sync'],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except Exception as e:
        return f'could not sync opportunity tracker: {type(e).__name__}: {e}'
    if res.returncode != 0:
        return 'could not sync opportunity tracker: ' + (res.stderr.strip() or res.stdout.strip())[:300]
    return None


def load_offline_items(batch_dir: Path | None = None) -> list[dict]:
    if batch_dir is not None:
        return wfg_phase1.load_items_from_batch(batch_dir)
    items: list[dict] = []
    for path in sorted(ARCHIVE.glob('raw-*.json')):
        try:
            data = json.loads(path.read_text(errors='replace'))
        except Exception:
            continue
        items.extend(x for x in (data.get('opportunitiesData') or data.get('data') or []) if isinstance(x, dict))
    return items


def print_entry(idx: int, item: dict, score: int, reasons: list[str], watch: list[str],
                profile: dict, descriptions: dict[str, str], compact: bool = False) -> None:
    title = str(item.get('title') or 'Untitled').strip()
    notice = str(item.get('noticeId') or item.get('solicitationNumber') or 'unknown').strip()
    if compact:
        days = deadline_days(item)
        due = f'{days}d' if days is not None else '?'
        print(f'{idx}. {title[:90]} | due {due} | {notice}')
        if watch:
            print('   ' + '; '.join(watch[:2]))
        return
    agency = str(item.get('fullParentPathName') or item.get('department') or 'Agency not listed').strip()
    due = str(item.get('responseDeadLine') or 'Deadline not listed').strip()
    days = deadline_days(item)
    if days is not None:
        due += f' ({days} days)'
    naics = ', '.join(item_naics(item)[:2])
    ev = estimated_value(item, descriptions)
    sa = str(item.get('typeOfSetAsideDescription') or item.get('typeOfSetAside') or 'Not listed')
    ctype = apparent_contract_type(item, descriptions)
    print(f'{idx}. {title}')
    print(f'   Score: {score} | Notice: {notice} | NAICS: {naics or "n/a"}')
    print(f'   Agency: {agency[:120]}')
    print(f'   Due: {due}')
    pl = pop_label(item)
    if pl:
        print(f'   Place: {pl[:100]}')
    if ev is not None:
        print(f'   Visible value/magnitude: ~${ev:,.0f}')
    print(f'   Set-aside eligibility: {sa} | Apparent contract type: {ctype}')
    if reasons:
        print('   Why it fits: ' + '; '.join(reasons[:3]))
    if watch:
        print('   Why it may not fit / warnings: ' + '; '.join(watch[:4]))
    print('   Confidence: medium (metadata-screened; solicitation/attachments not ingested until intake)')
    link = item_link(item)
    if link:
        print(f'   Link: {link}')
    print('')


def main() -> int:
    start = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='Score archived raw files instead of calling the API.')
    parser.add_argument('--no-sync', action='store_true', help='Skip Google Sheet tracker read/sync.')
    parser.add_argument('--no-final-sync', action='store_true', help='Skip only the post-brief Google Sheet tracker sync.')
    parser.add_argument('--seen-keys-file', help='Use a local JSON seen-key snapshot instead of reading the Google Sheet live.')
    parser.add_argument('--latest-seen-keys', action='store_true', help='Use the newest local seen-key snapshot from opportunity-searches/sam-api/snapshots/.')
    parser.add_argument('--fetch-only', action='store_true', help='Only fetch/archive SAM.gov raw pages; print only errors or warnings.')
    parser.add_argument('--batch-id', help='Explicit batch id for offline testing/replay.')
    args = parser.parse_args()

    profile = load_profile()
    runtime_warnings: list[str] = []
    now = dt.datetime.now().strftime('%a %b %d, %Y %I:%M %p')
    header = 'WFG SAM.gov Morning Opportunity Brief (v2)'
    api_key = get_key()

    batch_id = args.batch_id
    batch_dir = (wfg_phase1.BATCHES / args.batch_id) if args.batch_id else None

    if args.offline:
        if not batch_dir and args.latest_seen_keys:
            try:
                batch_id, batch_dir, _manifest = wfg_phase1.current_completed_batch_dir()
            except Exception as e:
                print(wfg_phase1.short_failure('brief', batch_id, str(e), stale_prevented=True))
                return 1
        items = load_offline_items(batch_dir)
        total, requests_used = len(items), 0
    else:
        if not batch_dir:
            try:
                batch_id, batch_dir, _manifest = wfg_phase1.create_batch()
            except Exception as e:
                print(wfg_phase1.short_failure('fetch', None, str(e), stale_prevented=True))
                return 1
        if not api_key:
            print(header)
            print(now)
            print('')
            print('SAM.gov API key is not configured, so the live search could not run.')
            print('Add SAM_GOV_API_KEY to /home/nick/.hermes/.env and restart the Hermes gateway.')
            print('Get a key: https://sam.gov/data-services/API')
            if batch_dir:
                wfg_phase1.mark(batch_dir, fetch_status='failed', errors=['SAM.gov API key missing'])
            return 1 if args.fetch_only else 0
        try:
            wfg_phase1.mark(batch_dir, fetch_status='running', fetch_started_at=wfg_phase1.utc_now())
            items, total, requests_used, runtime_warnings = fetch_paginated(api_key, profile, start, batch_dir)
            wfg_phase1.mark(batch_dir, fetch_status='completed', fetch_completed_at=wfg_phase1.utc_now(), records=len(items), api_pages=requests_used, warnings=runtime_warnings)
        except Exception as e:
            if batch_dir:
                wfg_phase1.mark(batch_dir, fetch_status='failed', errors=[f'{type(e).__name__}: {e}'])
                wfg_phase1.record_error(batch_id, 'fetch', e)
            print(header)
            print(now)
            print('')
            print(f'SAM.gov API search failed: {type(e).__name__}: {e}')
            print('Check the API key, daily rate limit, network, and SAM.gov status.')
            return 1 if args.fetch_only else 0

    if args.fetch_only:
        for warning in runtime_warnings:
            print(f'WFG SAM.gov raw fetch warning: {warning}')
        return 0

    if batch_dir:
        wfg_phase1.mark(batch_dir, brief_status='running')

    seen_keys: set[str] = set()
    tracker_warning = None
    seen_file = Path(args.seen_keys_file) if args.seen_keys_file else None
    if args.latest_seen_keys:
        seen_file = latest_seen_keys_file()
        if seen_file is None:
            tracker_warning = f'no local seen-key snapshot found in {SNAPSHOT_DIR}; dedupe is limited to this run'
    if seen_file is not None:
        seen_keys, tracker_warning = load_seen_keys_file(seen_file)
    elif not args.no_sync:
        seen_keys, tracker_warning = load_seen_tracker_keys()

    descriptions = load_desc_cache()
    duplicate_count = 0
    rejected = 0
    buckets: dict[str, list] = {'pursue': [], 'sources_sought': [], 'watch': []}
    seen_this_run: set[str] = set()
    for item in items:
        key = dedupe_key(item)
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        if key in seen_keys:
            duplicate_count += 1
            continue
        bucket, score, reasons, watch = classify(item, profile, descriptions)
        if batch_id:
            wfg_phase1.upsert_opportunity(item, key, batch_id, None if bucket == 'reject' else bucket, score)
        if bucket == 'reject':
            rejected += 1
            continue
        buckets[bucket].append((score, item, reasons, watch))
    for b in buckets.values():
        b.sort(key=lambda x: x[0], reverse=True)

    # Spend remaining API budget fetching real descriptions for top finalists,
    # then re-classify them (description text can reveal magnitude or risk).
    desc_budget = 0 if (args.offline or not api_key) else int(profile['api']['fetch_descriptions_for_top'])
    if desc_budget > 0 and buckets['pursue']:
        spent = 0
        finalists = [t[1] for t in buckets['pursue'][:desc_budget]]
        for item in finalists:
            if spent >= desc_budget:
                break
            if seconds_left(start) < 12:
                runtime_warnings.append('Skipped some description fetches to avoid cron timeout.')
                break
            if fetch_description(item, api_key, descriptions, start):
                spent += 1
        save_desc_cache(descriptions)
        refreshed = []
        for score, item, reasons, watch in buckets['pursue']:
            bucket, score2, reasons2, watch2 = classify(item, profile, descriptions)
            if bucket == 'pursue':
                refreshed.append((score2, item, reasons2, watch2))
            elif bucket != 'reject':
                buckets[bucket].append((score2, item, reasons2, watch2))
            else:
                rejected += 1
        buckets['pursue'] = sorted(refreshed, key=lambda x: x[0], reverse=True)
        buckets['watch'].sort(key=lambda x: x[0], reverse=True)

    brief = profile['brief']
    min_score = int(brief.get('min_score_pursue', 30))
    urgent_all = [t for t in buckets['pursue'] if deadline_days(t[1]) is not None and deadline_days(t[1]) < int(profile['deadlines']['urgent_below_days'])]
    urgent = urgent_all[:int(brief.get('max_urgent', 5))]
    urgent_keys = {dedupe_key(t[1]) for t in urgent}
    pursue = [t for t in buckets['pursue'] if t[0] >= min_score and dedupe_key(t[1]) not in urgent_keys][:int(brief['max_pursue'])]
    sources = buckets['sources_sought'][:int(brief['max_sources_sought'])]
    watching = buckets['watch'][:int(brief['max_watch'])]

    print(header)
    print(now)
    vb = profile['value_band']
    print(f'Profile: starter trades (NAICS/PSC gated), target value ${vb["ideal_min"]:,.0f}-${vb["ideal_max"]:,.0f}, hard cap ${vb["hard_max"]:,.0f}.')
    print(f'Scanned: {len(items)} of {total} posted | API requests: {requests_used} | Off-target/oversize/late: {rejected} | Already tracked: {duplicate_count}')
    if batch_id:
        print(f'Batch: {batch_id}')
    print(f'New today -> pursue: {len(buckets["pursue"])} | sources sought: {len(buckets["sources_sought"])} | watch: {len(buckets["watch"])}')
    if tracker_warning:
        print(f'Tracker warning: {tracker_warning}')
    for warning in runtime_warnings:
        print(f'Runtime warning: {warning}')
    print('')

    if not (pursue or sources or watching):
        print('No new target-fit opportunities in this window.')
        print('If this repeats for a week, widen naics_core/psc_prefixes in sam_search_profile.json or raise the value band.')
    if pursue:
        print('— TOP STARTER-FIT SOLICITATIONS (actionable now) —')
        for idx, (score, item, reasons, watch) in enumerate(pursue, 1):
            print_entry(idx, item, score, reasons, watch, profile, descriptions)
    if urgent:
        print('— URGENT: under normal tracker minimum; Telegram-only triage —')
        for idx, (score, item, reasons, watch) in enumerate(urgent, 1):
            print_entry(idx, item, score, reasons, watch, profile, descriptions, compact=True)
        print('')
    if sources:
        print('— SOURCES SOUGHT / RFIs (free relationship + set-aside shaping; respond with capability statement) —')
        for idx, (score, item, reasons, watch) in enumerate(sources, 1):
            print_entry(idx, item, score, reasons, watch, profile, descriptions, compact=True)
        print('')
    if watching:
        print('— WATCH (presolicitation or certification-gated) —')
        for idx, (score, item, reasons, watch) in enumerate(watching, 1):
            print_entry(idx, item, score, reasons, watch, profile, descriptions, compact=True)
        print('')

    if pursue:
        ids = ', '.join(str(t[1].get('noticeId') or '')[:12] for t in pursue[:3])
        print(f'Next action: reply with notice IDs to intake (e.g. "intake {ids}"). Marcus runs wfg-opportunity-intake then wfg-bid-no-bid before any pursuit.')

    if not args.no_sync and not args.no_final_sync:
        sync_warning = sync_tracker_after_run(start)
        if sync_warning:
            print(f'Tracker sync warning: {sync_warning}')
    if batch_dir:
        wfg_phase1.mark(batch_dir, enrichment_status='completed', brief_status='completed')
        wfg_phase1.atomic_write(wfg_phase1.LAST_SUCCESSFUL_BATCH, (batch_id or batch_dir.name) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
