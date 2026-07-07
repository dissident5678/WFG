# WFG Government Contracting System v2 — Contents & Deployment

Created: 2026-06-10. This folder is the complete enhanced system: every file
worth keeping from the v1 review package plus all v2 fixes already applied.
Read SYSTEM_REVIEW_AND_OPTIMIZATION.md for the why; this file is the what/how.

## What changed vs v1

| Area | Change |
|---|---|
| `gov-contracting/scripts/sam_morning_opportunity_brief.py` | **Rewritten.** Paginates full daily volume, NAICS/PSC/keyword target gate, domestic-only, value cap, set-aside routing, URGENT 7-14 day RFQs kept, description fetch for finalists, bucketed PURSUE/SOURCES SOUGHT/WATCH brief. |
| `gov-contracting/scripts/sync_sam_opportunity_tracker.py` | **Rewritten.** Imports scoring from the brief (no more drift), writes only target-fit rows to the Google Sheet, truncates raw_json, tags rows with bucket. |
| `gov-contracting/opportunity-searches/sam_search_profile.json` | **New.** Single editable source of truth for NAICS/PSC/keywords/value band/deadlines/regions/set-asides/API budget. The wfg-sam-api-watcher skill referenced this file; it now exists. |
| `gov-contracting/sam-api-morning-agent.md` | Rewritten for v2 behavior, tuning guide, API rate-limit budget. |
| `gov-contracting/templates/subcontract_agreement_template.md` | **New.** Full subcontract draft with FAR flow-downs, SCA exhibit, internal checklist. Attorney baseline required once. |
| `gov-contracting/templates/rfq_quick_kit.md` | **New.** Standing company-info block + RFQ package checklist. |
| `gov-contracting/templates/sources_sought_response_template.md` | **New.** Weekly relationship-building motion. |
| `gov-contracting/templates/sub_bench_plan.md` | **New.** Pre-bid subcontractor bench by trade with status ladder + pre-approved intro. |
| `gov-contracting/templates/past_performance_record.md` | **New.** One record per job, verified-only citation rule. |
| `active-hermes-skills/business-ops/wfg-bid-no-bid/SKILL.md` | v2: weighted starter-stage scorecard, hard NO-BID rules, profile feedback loop. |
| `active-hermes-skills/business-ops/wfg-sam-api-watcher/SKILL.md` | v2: profile-driven, bucket model, API budget rule. |
| `active-hermes-skills/business-ops/wfg-sub-agreement-drafter/SKILL.md` | **New skill** (23rd) for the sub-agreement step. |
| `gov-contracting/README.md`, `agent-team.md` | Starter strategy block + standing weekly motions + new subagent registered. |
| Everything else | Carried over unchanged (MARCUS.md, AGENTS.md, approval-gates.md, operations.md, remaining 20 skills, original templates, company docs). |

## Deployment to the Hermes box

The live system runs on the Hermes machine under `/home/nick`. From this Mac,
copy this folder over (or transfer however you normally move files), then on
the Hermes box:

```bash
# 1. Back up the live project
cp -a /home/nick/workspace/gov-contracting /home/nick/workspace/gov-contracting.bak-$(date +%Y%m%d)

# 2. Copy v2 files over the project (does not touch opportunities/, raw archives, .env)
cp -a WFG-government-contracting-system-v2/gov-contracting/. /home/nick/workspace/gov-contracting/

# 3. Update the cron's copy of the brief script (cron job b625160ab4b3 runs this path)
cp /home/nick/workspace/gov-contracting/scripts/sam_morning_opportunity_brief.py \
   /home/nick/.hermes/scripts/wfg_sam_morning_opportunity_brief.py

# 4. Install/refresh the skills
cp -a WFG-government-contracting-system-v2/active-hermes-skills/business-ops/. \
   /home/nick/.hermes/skills/business-ops/

# 5. Test offline first (no API spend)
cd /home/nick/workspace/gov-contracting
python3 scripts/sam_morning_opportunity_brief.py --offline --no-sync

# 6. Live test once (uses ~6-8 API requests of the ~10/day personal-key budget;
#    skip if the 7AM cron already ran today)
python3 scripts/sam_morning_opportunity_brief.py

# 7. Confirm the cron job still points at the .hermes script and delivery
#    target telegram:-1003889564123:104 — no cron change needed.
```

Notes:
- Old raw archive files (raw-YYYYMMDD-HHMMSS.json) and new paginated ones
  (raw-...-pN.json) coexist fine; the tracker reads both.
- The first v2 tracker sync will REPLACE the sheet contents with only
  target-fit rows. The old ~2,900-row dump disappears (it's still recoverable
  from the raw archive if ever needed).
- `WFG_PROJECT_DIR` env var overrides `/home/nick/workspace/gov-contracting`
  for testing on other machines.

## Tuning

Edit `gov-contracting/opportunity-searches/sam_search_profile.json` only:
- Empty briefs for a week → widen `naics_core`, raise `value_band.hard_max`,
  or lower `brief.min_score_pursue`.
- Noisy briefs → tighten keywords, raise `min_score_pursue`.
- Going after bigger work later → raise the value band; the rest adapts.
