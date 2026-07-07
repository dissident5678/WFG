#!/usr/bin/env python3
"""Fetch/archive raw SAM.gov pages for WFG morning pipeline.

Cron mode: quiet on success; prints warnings or fails loudly on SAM.gov/API errors.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2'))
SCRIPT = PROJECT / 'scripts' / 'sam_morning_opportunity_brief.py'

if __name__ == '__main__':
    raise SystemExit(subprocess.call([sys.executable, str(SCRIPT), '--fetch-only']))
