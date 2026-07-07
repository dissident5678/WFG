#!/usr/bin/env python3
"""Post-brief sync of archived SAM.gov raw results into WFG Google Sheet tracker.

Cron mode: quiet on success, non-zero + output on failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SYNC_SCRIPT = Path('/home/nick/workspace/gov-contracting/scripts/sync_sam_opportunity_tracker.py')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    res = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), '--sync'],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if res.returncode != 0:
        print('WFG SAM.gov tracker post-brief sync failed.')
        detail = (res.stderr.strip() or res.stdout.strip())[:1000]
        if detail:
            print(detail)
        return res.returncode or 1
    if args.verbose:
        try:
            print(json.dumps(json.loads(res.stdout), indent=2))
        except Exception:
            print(res.stdout.strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
