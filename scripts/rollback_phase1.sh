#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=${1:-/home/nick/workspace/gov-contracting/backups/phase1-20260624-091204}
cp "$BACKUP_DIR/project-scripts/sam_morning_opportunity_brief.py" /home/nick/workspace/gov-contracting/scripts/sam_morning_opportunity_brief.py
cp "$BACKUP_DIR/project-scripts/sync_sam_opportunity_tracker.py" /home/nick/workspace/gov-contracting/scripts/sync_sam_opportunity_tracker.py
cp "$BACKUP_DIR/hermes-scripts/wfg_sam_tracker_snapshot.py" /home/nick/.hermes/scripts/wfg_sam_tracker_snapshot.py
cp "$BACKUP_DIR/hermes-scripts/wfg_sam_raw_fetch.py" /home/nick/.hermes/scripts/wfg_sam_raw_fetch.py
cp "$BACKUP_DIR/hermes-scripts/wfg_sam_brief_deliver.py" /home/nick/.hermes/scripts/wfg_sam_brief_deliver.py
cp "$BACKUP_DIR/hermes-scripts/wfg_sam_tracker_sync.py" /home/nick/.hermes/scripts/wfg_sam_tracker_sync.py
chmod +x /home/nick/.hermes/scripts/wfg_sam_*.py
printf 'Restored Phase 1 changed scripts from %s\n' "$BACKUP_DIR"
