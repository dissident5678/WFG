# WFG Government Contracting System — Review & Optimization Report

Reviewed: 2026-06-10. Source: WFG-government-contracting-system-review package
(29 days of live SAM.gov pulls, 22 Hermes skills, scripts, docs, templates).
This folder (WFG-government-contracting-system-v2) is the enhanced system:
everything worth keeping, plus every fix below already applied.

---

## 1. The headline problem: why you were seeing big fish

You were right that the system wasn't finding starter opportunities. Verified
against your own archived data:

1. **You were screening ~3-5% of the market.** The v1 script pulled the first
   100 records of 1,000-3,700 notices posted per day, with no NAICS, PSC, or
   set-aside filter. SAM returns DLA spare-parts solicitations first (aircraft
   parts, valves, fasteners — the top NAICS codes in your archive), so the 100
   records were almost all noise. Only ~9% of captured items were in starter
   trades at all.
2. **Scoring was blind.** The v2 search API returns a *URL* in the
   `description` field, not text. The keyword and dollar-value matching only
   ever saw titles, so the $500K screen almost never triggered — which is
   exactly why oversized projects leaked into your briefs.
3. **Generic keywords matched big projects.** "repair", "maintenance",
   "installation" scored points on multi-million-dollar industrial notices.
4. **Bug:** the "cui" risk keyword substring-matched "CIRCUIT", flagging
   circuit-breaker notices as CUI risks.
5. **The 14-day deadline filter threw away the starter zone.** Small RFQs
   often post with 7-14 day windows; those were silently dropped.

### What v2 does instead (already built and validated)

`scripts/sam_morning_opportunity_brief.py` + `opportunity-searches/sam_search_profile.json`:
- Paginates the full daily volume (limit=1000/request, 2-4 requests).
- Gates on a starter-trade profile (25 core NAICS, S2/Z1/Z2 PSC prefixes,
  specific scope keywords) before scoring — DLA noise never reaches you.
- Rejects overseas places of performance and visible values > $500K.
- Routes certification-gated set-asides (WOSB/SDVOSB/8a/HUBZone) to a WATCH
  bucket until your certifications are verified — they're future pipeline, not
  noise, but you can't bid them yet.
- Keeps 7-14 day RFQs, marked URGENT.
- Spends leftover API budget fetching real description text for top finalists,
  then re-screens for magnitude ("...magnitude between $100,000 and $250,000"
  lives in description text) and risk terms. Word-boundary matching fixes the
  CIRCUIT/cui bug.
- Output is bucketed: PURSUE / SOURCES SOUGHT / WATCH, with a copy-paste
  intake command at the bottom.

**Validation on your own 29 days of archived data:** v2 surfaces 46 domestic
starter-fit solicitations (fence repair at DSC Richmond VA, NIH facility
maintenance in Bethesda MD, specialty cleaning BPA at Dover AFB DE, NPS
electrical work, USACE mowing...) that v1's pipeline either never fetched or
buried. And that's from the crippled 100-record samples — live v2 screens the
full 1,000-3,700/day.

**Rate-limit caution:** non-federal personal SAM API keys allow ~10
requests/day. v2 budgets 2-4 search + up to 4 description requests. Don't run
it twice a day; don't raise the budgets past ~9 total.

All search tuning now happens in `sam_search_profile.json` — never in code.
Both scripts read it, and the tracker sync imports the brief's scoring
functions, so the two can no longer drift apart (in v1 they were two diverging
copies).

---

## 2. Strategy upgrades for a starter contractor (biggest non-code wins)

1. **Work the SOURCES SOUGHT bucket — it's free pipeline.** Responding to
   sources-sought notices puts WFG in front of contracting officers before
   solicitations exist and can shape requirements into small-business
   set-asides. Target 2-3/week. Template added:
   `templates/sources_sought_response_template.md`.
2. **Build the sub bench BEFORE bidding.** A 10-day RFQ is only winnable if
   subs are already validated. Plan + standing weekly motion added:
   `templates/sub_bench_plan.md`. Janitorial and grounds first — they're the
   most frequent starter solicitations.
3. **Treat the first wins as past-performance purchases.** A tiny $8K job is a
   real federal past-performance citation. Record every one:
   `templates/past_performance_record.md`. Consider also subcontracting to
   established primes (SBA SubNet, large primes' supplier portals) purely to
   build performance history.
4. **Stay in the simplified-acquisition zone.** $10K-$250K = FAR Part 13:
   short quotes, fast awards, less competition from sophisticated bidders.
   The profile encodes this band; the bid/no-bid skill now scores against it.
5. **Don't ignore state/local.** Maryland eMMA, VA eVA, and PA COSTARS list
   smaller, less-competed work and build the same past performance. Worth a
   future watcher script (not built yet — SAM first).
6. **Ask for a debrief on every loss** and log it via wfg-historian-tracker;
   loss reasons are how the profile and pricing get tuned.

---

## 3. Stage-by-stage findings and changes

### Finding opportunities
- Rebuilt (section 1). Also: the wfg-sam-api-watcher skill referenced a
  `sam_search_profile.json` that didn't exist — it does now, and the skill
  (v2) documents the bucket model and API budget.

### Rating / bid-no-bid
- The old skill said "score capability fit" with no rubric. v2 skill has a
  weighted 9-factor starter-stage scorecard (value band 20%, sub coverage 15%,
  procedure complexity 15%...), hard NO-BID rules (certification-gated
  set-asides, construction ≥$150K until bonding verified, >$500K), and a
  feedback step: every NO-BID reason gets recorded to tune the search profile.

### Finding subs
- Scout skill was fine but reactive. Added the proactive bench plan with
  status ladder (EMPTY → CANDIDATES → CONTACTED → VALIDATED → PROVEN) and a
  pre-approved intro message so outreach batches need one approval, not
  per-email approvals.

### Outreach
- Templates kept. The quote-request template should always state the wage
  determination when SCA applies (subs must price WD rates) — noted in the
  RFQ kit and sub agreement. Recommend a standing-send rule (Managing Member
  pre-approves the bench intro + quote request templates) so Marcus can send
  without per-message gates; per-message approval was a bottleneck by design
  but is unnecessary for approved templates.

### Organizing / filling documents
- Opportunity folder template kept (it's good). Added
  `templates/rfq_quick_kit.md`: the standing company-info block + standard
  attachments + RFQ package checklist, so simple quotes assemble in under an
  hour. Keep `company-docs/` (W-9, capability statement, COI, SAM record)
  current — it was on the blocker list; it's the highest-leverage paperwork
  you can do before the next bid.

### Sub agreements (was a complete gap)
- Nothing existed between "sub quoted" and "we won." Added:
  - `templates/subcontract_agreement_template.md` — full draft agreement with
    FAR flow-downs, SCA wage-determination exhibit, insurance prerequisites,
    payment terms, and an internal pre-send checklist. One-time attorney
    review required before first use.
  - New skill `wfg-sub-agreement-drafter` — assembles the package from scope
    sheets + quote + clause-librarian flow-downs, runs the LOS sanity check,
    and stops for Managing Member approval.

### Government-rules compliance (gaps now covered)
- **SCA wage determinations** — janitorial/grounds/most facility services are
  Service Contract Act work; pricing must use the WD rates and flow them to
  subs. Now flagged by the search (wage keywords), the bid scorecard, the RFQ
  kit, and the sub agreement. This was absent everywhere in v1 and is the
  most common way new service contractors lose money.
- **Miller Act bonds** — construction ≥$150K flagged in search and a hard
  bid/no-bid gate until bonding is verified.
- **Set-aside eligibility** — v1 mildly penalized WOSB/SDVOSB notices (-10);
  v2 hard-routes them to WATCH. You cannot bid them until certified; the
  51/49 control rules your docs already handle remain the eligibility key.
- Existing strengths kept: LOS calculator (52.219-14), approval gates,
  no-unverified-claims rules, CUI gatekeeper, amendment freeze.

### Process / efficiency
- Tracker sync v2: only target-fit rows go to the Google Sheet (v1 wrote all
  ~2,900 deduped records — mostly spare parts), raw_json truncated under the
  50K-char cell limit, bucket tag added to fit_reasons for filtering.
- Scoring logic now single-sourced (sync imports from the brief).
- Both scripts honor `WFG_PROJECT_DIR` for testing; `--offline` and
  `--no-sync` flags let you test without spending API requests.

---

## 4. Recommended next steps (in order)

1. Deploy v2 (see README_V2_UPGRADE.md) and watch the brief for 3-4 days.
   Expect several PURSUE items daily instead of noise.
2. Finish the blocker list basics: SAM registration/UEI, bank account,
   bids@ email, W-9, 1-page capability statement (the RFQ kit and
   sources-sought template both depend on it).
3. Start the two standing weekly motions (sources-sought responses, sub bench
   building) — they generate pipeline while you wait for the right RFQ.
4. Have an attorney baseline the subcontract template once.
5. Add the missing crons from the expansion pack when ready: Friday pipeline
   digest first (highest value), then deadline digest and amendment monitor.
6. After the first 5 bids, review loss reasons and tune
   sam_search_profile.json (value band, regions, NAICS weights).
7. Later: eMMA/eVA state watchers; WOSB/VetCert certification path to unlock
   the WATCH bucket; persistent worker profiles if on-demand subagents feel
   slow.

## 5. What was intentionally NOT carried into v2

- `imports/complete-hermes-architecture-20260520/` — original blueprint
  zips/docx; historical reference, stays in the review package and on the
  Hermes box.
- `opportunity-searches/sam-api/raw-*.json` — operational data generated
  daily on the Hermes box; doesn't belong in the system definition.
- `package-manifest.txt`, `package-summary.txt`, `README_REVIEW_PACKAGE.md` —
  artifacts of the review packaging itself.
- `BLUEPRINT_IMPLEMENTATION_STATUS.md` — superseded by this report and
  README_V2_UPGRADE.md (the old file described the v1 state).
