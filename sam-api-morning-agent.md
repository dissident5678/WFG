# WFG SAM.gov Morning Opportunity Agent (v2)

Purpose: run a daily SAM.gov Opportunities API search for starter-friendly
federal opportunities that fit Wright Foster Group LLC's prime-contractor /
local-subcontractor model.

## Schedule

Hermes cron job: WFG SAM.gov Morning Opportunity Brief
Job ID: b625160ab4b3
Schedule: 7:00 AM Eastern every day
Script: /home/nick/.hermes/scripts/wfg_sam_morning_opportunity_brief.py
Project copy: /home/nick/workspace/wfg-gov-contracting-v2/scripts/sam_morning_opportunity_brief.py

Both copies must be the same v2 file. The tracker sync script imports scoring
from the project copy, so keep them in sync when updating.

## How v2 searches (and why v1 missed starter opportunities)

v1 pulled only the first 100 of 1,000-3,700 daily notices with no filters —
a sample dominated by DLA spare-parts solicitations — so janitorial, grounds,
painting, and repair work almost never reached scoring.

v2:
1. Paginates the full posted window (limit=1000 per request, usually 2-4
   requests) so every notice is screened.
2. Gates on the target profile in
   `opportunity-searches/sam_search_profile.json`: starter NAICS codes,
   S2/Z1/Z2 PSC prefixes, and strong scope keywords. Everything else is
   rejected before scoring.
3. Rejects overseas places of performance and visible values over the hard
   cap ($500K), and screens certification-gated set-asides (WOSB/SDVOSB/8(a)/
   HUBZone) into a WATCH bucket until WFG's certifications are verified.
4. Keeps urgent 7-14 day RFQs (marked URGENT) instead of dropping them —
   small fast-turnaround quotes are exactly the starter zone.
5. Spends up to 4 remaining API requests fetching real description text for
   top finalists (the search API returns a URL, not text), then re-screens
   them for dollar magnitude and risk terms.
6. Buckets the brief: PURSUE (actionable solicitations) / SOURCES SOUGHT
   (respond with capability statement to build agency relationships) /
   WATCH (presolicitations and certification-gated).

## Tuning the search

Edit `opportunity-searches/sam_search_profile.json` — never the script — to:
- add/remove NAICS codes or PSC prefixes
- change the value band (default ideal $10K-$250K, hard cap $500K)
- change deadline rules, region boost states, keyword lists
- raise/lower how many items each bucket shows

If the brief is empty for a week, widen `naics_core` or raise
`value_band.hard_max`. If it's noisy, tighten the keyword lists or raise
`brief.min_score_pursue`.

## API rate limit (important)

Non-federal personal SAM.gov API keys are limited to roughly 10 requests/day.
A v2 run uses 2-4 search requests + up to `fetch_descriptions_for_top` (4)
description requests, staying under that limit with no second run that day.
Do not run the brief twice on the same day with a personal key, and do not
raise `max_search_requests_per_run` + `fetch_descriptions_for_top` above ~9
total. If the API starts returning rate-limit errors, the brief will say the
search failed; check https://open.gsa.gov/api/get-opportunities-public-api/.

## Important limitations

SAM.gov records rarely expose a reliable dollar value in search results.
Construction notices usually state magnitude in the description text, which v2
reads only for top finalists. "No visible value" in the brief means exactly
that — the intake subagent must confirm magnitude from the solicitation
documents before bid/no-bid.

The morning brief does not decide to bid. Any interesting notice goes through:
1. Opportunity Intake Subagent using wfg-opportunity-intake.
2. Bid/No-Bid Strategy Subagent using wfg-bid-no-bid.
3. Solicitation Reader Subagent using wfg-solicitation-reader if still promising.

## API key setup

Preferred variable name: SAM_GOV_API_KEY
Secret file: /home/nick/.hermes/.env

Add a line like this to /home/nick/.hermes/.env:

SAM_GOV_API_KEY=your_key_here

Then restart the gateway so scheduled jobs inherit it:

hermes gateway restart

Do not paste the key into Telegram, chat, project markdown files, or Kanban cards.

## Telegram delivery

Operational target: Wright Foster Group HQ / WFG Opportunity Briefs
Telegram delivery string: telegram:-1003889564123:104
Telegram topic/thread ID: 104

If the Telegram group is ever replaced:
1. Create or identify the new Telegram forum topic/channel.
2. Add the Hermes bot and make sure it can post.
3. Use Telegram Bot API or Hermes delivery metadata to identify the chat ID and topic/thread ID.
4. Update cron job b625160ab4b3 delivery to telegram:<chat_id>:<thread_id>.
5. Run the cron job once manually and verify the briefing appears in the target topic.

## Testing without spending API requests

WFG_PROJECT_DIR=<project> python3 scripts/sam_morning_opportunity_brief.py --offline --no-sync

scores the archived raw-*.json files instead of calling the API.
