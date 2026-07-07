#!/usr/bin/env python3
"""Snapshot current Google Sheet SAM.gov tracker dedupe keys for WFG.

Cron mode: quiet on success, non-zero + stderr/stdout on failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
SNAPSHOT_DIR = PROJECT / 'opportunity-searches' / 'sam-api' / 'snapshots'
SYNC_SCRIPT = PROJECT / 'scripts' / 'sync_sam_opportunity_tracker.py'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = SNAPSHOT_DIR / f'seen-keys-{stamp}.json'
    res = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), '--print-seen-keys'],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
    )
    if res.returncode != 0:
        print('WFG SAM.gov tracker snapshot failed: could not read Google Sheet tracker.')
        detail = (res.stderr.strip() or res.stdout.strip())[:800]
        if detail:
            print(detail)
        return res.returncode or 1
    keys = sorted({line.strip() for line in res.stdout.splitlines() if line.strip()})
    payload = {
        'created_at': dt.datetime.now().isoformat(timespec='seconds'),
        'source': 'Google Sheet tracker via sync_sam_opportunity_tracker.py --print-seen-keys',
        'count': len(keys),
        'seen_keys': keys,
    }
    out.write_text(json.dumps(payload, indent=2))
    latest = SNAPSHOT_DIR / 'latest-seen-keys.json'
    latest.write_text(json.dumps(payload, indent=2))
    if args.verbose:
        print(json.dumps({'snapshot': str(out), 'latest': str(latest), 'count': len(keys)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
