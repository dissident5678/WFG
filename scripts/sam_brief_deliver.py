#!/usr/bin/env python3
"""Deliver WFG SAM.gov morning brief from archived raw pages.

Reads the latest local seen-key snapshot created before the raw fetch, does not
call the SAM.gov search API, and does not sync the tracker. Cron stdout is the
Telegram-ready morning brief.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
SCRIPT = PROJECT / 'scripts' / 'sam_morning_opportunity_brief.py'

if __name__ == '__main__':
    raise SystemExit(subprocess.call([
        sys.executable,
        str(SCRIPT),
        '--offline',
        '--latest-seen-keys',
        '--no-final-sync',
    ]))
