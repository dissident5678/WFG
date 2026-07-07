#!/usr/bin/env python3
"""One-time/local migration: index existing SAM.gov raw archive into Phase 1 SQLite state."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sam_morning_opportunity_brief as brief
import sync_sam_opportunity_tracker as tracker
import wfg_phase1


def main() -> int:
    wfg_phase1.init_db()
    profile = brief.load_profile()
    descriptions = brief.load_desc_cache()
    records = tracker.load_archived()
    source_batch = 'historical-archive-index'
    count = 0
    for key, rec in records.items():
        bucket, score, reasons, watch = brief.classify(rec['item'], profile, descriptions)
        wfg_phase1.upsert_opportunity(rec['item'], key, source_batch, None if bucket == 'reject' else bucket, score)
        count += 1
    print(f'Indexed {count} deduplicated historical SAM.gov archive records into {wfg_phase1.DB_PATH}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
