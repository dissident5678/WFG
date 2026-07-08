# WFG Hermes Digital Employee Consensus Plan

Date: 2026-07-07
Author: Agent A
Reviewed and revised by: Agent B (Turn 1, 2026-07-07)
Reviewed and revised by: Agent A (Turn 2) and Agent B (Turn 2), 2026-07-07
Reviewed and revised by: Agent A (Turn 3) and Agent B (Turn 3), 2026-07-07
Reviewed and confirmed by: Agent A (Turn 4), 2026-07-07
Status: Final consensus — debate closed; implementation-ready. Next step is execution, starting with Phase 1.

## 1. Executive Summary

Hermes should become a controlled digital employee for Wright Foster Group government contracting operations.

The target system is not a chatbot that waits for Nick to decide every small step. It is an operating loop that finds SAM.gov opportunities, organizes source documents, reads solicitations, drafts analysis, prepares subcontractor-facing bid packets, drafts outreach, collects quotes, assembles pricing and proposal packages, and keeps Nick able to review and approve from Google Drive, Gmail drafts, Telegram, and a command dashboard.

The core rule is simple: Hermes does the legwork, Nick or the authorized human approver approves important actions before anything external, binding, financial, or reputational happens.

The operating model should be:

1. Discover and intake opportunities.
2. Build a local and Google Drive opportunity folder.
3. Read source documents and extract facts with a source map.
4. Recommend whether WFG should pursue.
5. Request Gate 1 approval before live pursuit.
6. After approval, queue subcontractor sourcing, packet building, and outreach drafting.
7. Request approval before any subcontractor outreach.
8. Execute only approved outreach, then log proof.
9. Track responses and quotes.
10. Normalize quotes, identify risk, and recommend a bid strategy.
11. Request approval before relying on subcontractors, pricing, or final bid strategy.
12. Assemble proposal files.
13. Request final package and submission approval.
14. Preserve submission proof and archive the matter.

The repository already contains important pieces of this system. The remaining work is to make the handoffs, roles, Drive hub, approvals, and dashboard consistent enough that Hermes always knows the next safe task.

## 2. Current State Assessment

### What the upgraded package already contains

The extracted project root inspected for this plan is:

```text
/Users/nickwright87/WFG/wfg_upgraded/workspace/wfg-gov-contracting-v2
```

The live Hermes path referenced by most scripts and docs is:

```text
/home/nick/workspace/wfg-gov-contracting-v2
```

High-value current assets:

- `README.md`, `AGENTS.md`, `operations.md`, `agent-team.md`, `approval-gates.md`, and `approval-hub.md` define the WFG business rules, subagent model, approval routing, and non-binding drafting policy.
- `docs/upgrade/README_DIGITAL_EMPLOYEE_UPGRADE.md` summarizes the upgrade and smoke tests.
- `docs/upgrade/DIGITAL_EMPLOYEE_OPERATING_MODEL.md` defines the digital employee loop and state machine.
- `docs/upgrade/SUBAGENT_SUITE_AND_PROFILES.md` and `agents/WFG_SUBAGENT_PROFILES.md` define role-bound subagents.
- `config/subagents.json` lists subagent roles and assigned skills, though some skill names need alignment with installed skill names.
- `config/drive-review-hub.json` defines the private Google Drive review structure.
- `config/approval-routing.json` maps approved gates to operational Telegram topics.
- `templates/subcontractor_bid_packet/` contains:
  - `WFG_Subcontractor_Bid_Packet_Template.docx`
  - `Hermes_Subcontractor_Bid_Packet_Instructions.docx`
  - `README.md`
- `scripts/wfg_sub_bid_packet.py` builds a clean subcontractor-facing packet plus internal review files and optional private Drive upload.
- `scripts/wfg_workflow_pump.py` reconciles approval buttons, dispatches follow-on internal tasks, and logs pump runs.
- `scripts/wfg_approval_dispatcher.py` maps approved gates to next internal Kanban tasks and uses idempotency keys.
- `scripts/reconcile_wfg_approval_buttons.py` reconciles Telegram button decisions into the workflow database and central approval folders.
- `scripts/send_wfg_approval_buttons.py` posts button-enabled approval packets to Telegram.
- `scripts/wfg_email_draft_sync.py` and `scripts/wfg_gmail_drafts.py` create Gmail drafts, never sends, and now block placeholder recipients.
- `scripts/wfg_tracking_schema.py` creates normalized tables for Gmail drafts, email response items, and subcontractor interactions.
- `scripts/wfg_phase1.py`, `scripts/wfg_phase2.py`, `scripts/wfg_phase3.py`, and `scripts/wfg_phase4.py` provide the older but useful deterministic pipeline, intake, approval, subcontractor, quote, pricing, compliance, proposal, and hardening foundations.
- `state/wfg_workflow.sqlite3` exists as the current workflow state database.
- `subcontractors/subcontractor_master.csv` and `.md` provide a 52-record subcontractor CRM export.
- `obsidian-vault/` contains opportunity notes, company/contact notes, email notes, and dashboards.
- `cloudflare-dashboard/` and `scripts/wfg_static_dashboard.py` provide a static operations dashboard foundation.
- The extracted `.hermes` folder one level above the repo contains cron jobs, skills, and wrapper scripts. The approval reconciler wrapper now calls `scripts/wfg_workflow_pump.py`.

### What still needs work

- The nested project root is not currently a Git repository in this extraction. Phase 1 should either initialize Git or merge this package into the live tracked repo before GitHub push.
- Several scripts default to old paths. Agent B verified the exact set. Env-overridable but wrong default: `scripts/sam_morning_opportunity_brief.py`, `scripts/sync_sam_opportunity_tracker.py`, `scripts/wfg_phase1.py`. Hardcoded with no env override at all: `scripts/sam_brief_deliver.py`, `scripts/sam_raw_fetch.py`, `scripts/sam_tracker_snapshot.py`, `scripts/sam_tracker_sync.py`, `scripts/run_test_intake_selected.py`. All must be unified behind `WFG_PROJECT_DIR` with the v2 path as default.
- `config/subagents.json` skill references are worse than "some mismatches": 8 of 13 subagents point at skills that do not exist under `.hermes/skills/business-ops/`. Verified mapping that Phase 1 must apply:
  - `wfg-sam-brief` -> no installed skill; closest is `wfg-sam-api-watcher` (rename or create)
  - `wfg-solicitation-analyst` -> `wfg-solicitation-reader`
  - `wfg-subcontractor-crm` -> `wfg-sub-crm-manager`
  - `wfg-estimating` -> `wfg-estimator-los`
  - `wfg-compliance-review` -> `wfg-compliance-auditor`
  - `wfg-proposal-assembler` -> `wfg-proposal-compiler`
  - `wfg-red-team-review` -> no installed skill; closest is `wfg-validator-pro` (rename or create)
  - `wfg-drive-review-hub` -> no skill exists anywhere; Drive logic currently lives only in `scripts/wfg_sub_bid_packet.py`
  Rule: rename config entries to installed skill names. Do not create new alias skill folders; duplicate SOPs are how the system confuses itself.
- The approval dispatcher infers downstream actions from gate-name substrings (`"Pursue" in gate`, `"Outreach" in gate`, `"Price" in gate`, `"Submission" in gate` at `scripts/wfg_approval_dispatcher.py:157-187`). This is not merely brittle — it interacts dangerously with this plan's own gate design. Any approval whose title contains the word "Outreach" (for example an approval of an outreach *draft*) would be dispatched as `gate2_outreach_execution`. Explicit gate IDs are a hard prerequisite for any new gate names. See the sequencing constraint in Section 5.
- Telegram approval buttons currently support only Approve and Deny (`scripts/send_wfg_approval_buttons.py:145-146`), and the reconciler only handles `approved`/`denied` and records the negative decision as `rejected` (`scripts/reconcile_wfg_approval_buttons.py:112-123`). The approval state model in this plan requires `revise_requested` and `held`, and requires the decision vocabulary to be standardized to one set of words.
- The current Drive upload is strongest for subcontractor packet/internal files. The full Google Drive Review Hub tree should be created and synchronized for each opportunity.
- Approval packets exist, but the required gates need to be standardized beyond the current four broad gates.
- Duplicate prevention is partially present through approval dispatch idempotency keys, Gmail draft tracking, and interaction tables. It needs a single external action ledger so outreach, follow-ups, and submissions cannot run twice.
- The live `.hermes` cron log shows a prior Gmail draft sync failure caused by placeholder recipients. The workspace script now includes guards, so deployment must confirm live scripts are updated and the failing scenario is covered by a regression test.
- The dashboard exists but is more CRM/communications focused than a daily command center for Nick. It should surface waiting approvals, next recommended actions, risk, deadlines, and pipeline money.
- Old upgrade docs, old phase docs, `.hermes` skills, and active repo files overlap. Hermes needs explicit replacement instructions to avoid mixing old behavior with the upgraded model.
- There is no test coverage for the most safety-critical code: `wfg_workflow_pump.py`, `wfg_approval_dispatcher.py`, `reconcile_wfg_approval_buttons.py`, and `send_wfg_approval_buttons.py` have zero tests. The existing `tests/` folder covers phases 1-4, the email response assistant, and the sub bid packet only. This must be fixed in Phase 2, not Phase 8.
- Verified positive: no currently callable repo script should be treated as the approved send worker. The current Gmail scripts are drafts-oriented, but the extracted database contains historical sent-proof rows (`gmail_drafts.status/sent_at`, `subcontractor_interactions`, and sent-related `workflow_events`). The new send execution worker in this plan is greenfield and must be ledger-checked from its first line; historical sent proof must be backfilled into the ledger before that worker is enabled.
- `docs/upgrade/HERMES_REPLACEMENT_INSTRUCTIONS.md` overlaps Section 12 of this plan. Two live replacement-instruction documents is exactly the "confusing itself with old instructions" failure mode. Phase 1 must reduce the old file to a pointer stub (see Section 12).

## 3. Digital Employee Operating Model

Hermes should behave like an operations employee with a daily inbox, clear task ownership, and a recoverable work log.

### Daily inbox and checklist

Every day Hermes should prepare one command summary for Nick:

- New SAM.gov opportunities worth review.
- Active opportunities by stage.
- Approvals waiting on Nick.
- Deadlines in the next 3, 7, and 14 days.
- Subcontractor outreach waiting to send.
- Quote requests sent and responses due.
- Missing documents, unclear dates, amendment risk, and compliance blockers.
- Drafts ready for Drive/Gmail review.
- Recommended next actions, grouped by urgency.
- Items blocked by credentials, API limits, Drive/Gmail errors, or missing human facts.

The inbox should be built from:

- `state/wfg_workflow.sqlite3`
- `approvals/pending/`
- `approvals/decision-log.md`
- opportunity folders under `opportunities/`
- `subcontractors/subcontractor_master.csv`
- `gmail_drafts`, `subcontractor_interactions`, and `email_response_items` tables
- Drive review bundle manifests
- `state/workflow-pump-runs/`
- Obsidian dashboard notes

### Opportunity intake

When Nick selects an opportunity or the morning brief identifies a strong candidate, Hermes should:

1. Create or update an opportunity record.
2. Create a durable opportunity folder.
3. Save the SAM.gov record and source link.
4. Download or register solicitation attachments.
5. Extract text where possible.
6. Write an attachment manifest.
7. Write initial drafts. Current reality: `scripts/wfg_phase2.py` already writes `00` through `12` below into both `drafts/` and compatibility copies at the opportunity root, so Phase 1 should document and preserve that behavior rather than undercount it:
   - `00_INTAKE.md`
   - `01_BID_NO_BID_SCORECARD.md`
   - `02_SOLICITATION_BRIEF.md`
   - `03_COMPLIANCE_MATRIX.md`
   - `04_MISSING_INFORMATION.md`
   - `05_SCOPE_DECOMPOSITION.md`
   - `06_SUBCONTRACTOR_SOURCING_CRITERIA.md`
   - `07_DRAFT_OUTREACH.md`
   - `08_PRICING_ASSUMPTIONS.md`
   - `09_TECHNICAL_PROPOSAL_SKELETON.md`
   - `10_REQUIRED_FORMS_CHECKLIST.md`
   - `11_SUBMISSION_CHECKLIST.md`
   - `12_RISK_REGISTER.md`
8. Create Gate 1 approval when the pursue recommendation is ready.

Gate 1 approval packet contents, matched to what exists today:

- Required for Gate 1: `00_INTAKE.md`, `01_BID_NO_BID_SCORECARD.md`, `02_SOLICITATION_BRIEF.md`, `03_COMPLIANCE_MATRIX.md`, `04_MISSING_INFORMATION.md`, `05_SCOPE_DECOMPOSITION.md`, `attachment_manifest.md`, source link, version hash, and deadline summary.
- Optional but useful for Gate 1 context: `06_SUBCONTRACTOR_SOURCING_CRITERIA.md`, `07_DRAFT_OUTREACH.md`, `08_PRICING_ASSUMPTIONS.md`, `09_TECHNICAL_PROPOSAL_SKELETON.md`, `10_REQUIRED_FORMS_CHECKLIST.md`, `11_SUBMISSION_CHECKLIST.md`, and `12_RISK_REGISTER.md`.
- Gate 1 does not approve any of the optional downstream drafts for external use. It only approves moving into active internal pursuit.

### Analysis

Hermes should automatically perform safe internal work:

- Extract deadlines, submission method, NAICS/PSC, set-aside, place of performance, buyer office, forms, attachments, Q&A, site visits, wage determinations, bonds, insurance, licenses, security, safety, and special clauses.
- Create a source map for every major extracted fact.
- Flag conflicts, amendments, missing attachments, and unparseable files.
- Produce a recommendation, not just a summary.

### Drafting

Hermes may draft:

- bid/no-bid scorecards
- solicitation summaries
- subcontractor sourcing lists
- subcontractor bid packets
- outreach emails
- follow-up emails
- quote comparison sheets
- pricing worksheets
- compliance checklists
- proposal sections
- submission checklists
- archive/closeout summaries

Drafting is internal and does not require approval. Use clear placeholders when facts are missing.

### Approvals

Hermes must stop before any external, binding, financial, or reputational action:

- external email, message, call, form, or outreach
- agency or contracting officer contact
- subcontractor quote reliance
- final price or markup decision
- representation or certification
- signature
- proposal, quote, bid, response, or amendment submission
- award/PO/contract/modification/NTP acceptance
- spending or commitment
- external sharing of sensitive files

Approvals must identify:

- exact gate
- exact action
- exact files
- exact recipients if outreach
- exact price or package if pricing/submission
- version and hash
- expiration/invalidation condition
- approver identity
- timestamp
- next state

### Follow-up

After approval, Hermes should not simply mark the packet approved. The workflow pump should queue the next safe internal task, and a downstream worker should execute only what is approved.

Examples:

- Gate 1 pursuit approval queues subcontractor sourcing, packet building, and Gate 2 prep.
- Gate 2 send approval queues approved outreach execution and proof logging.
- Gate 3 bid strategy approval queues proposal assembly.
- Gate 4 final package approval queues human submission handoff and proof archive.

### Logging

Every major step must leave a recoverable trail:

- `workflow_events` row in SQLite
- approval packet or decision row when applicable
- local artifact path
- Drive link when available
- version/hash
- actor/subagent
- timestamp
- status transition
- error or blocker note when incomplete

### Reminders

Hermes should create reminders for:

- government response deadline
- internal subcontractor quote deadline
- Q&A deadline
- site visit deadline
- quote follow-up date
- final review date
- amendment review check
- submission proof due after human submission

Reminder records should be stateful tasks, not just text in chat.

### Exception handling

If Hermes cannot safely continue, it should create a blocker note instead of guessing.

Blocker notes must include:

- what failed
- why it matters
- files reviewed
- source path or event ID
- recommended next action
- whether approval is required
- whether the opportunity should pause, revise, or close

## 4. Agent Suite Architecture

Skills are SOPs. Agents are role-bound workers with missions, boundaries, tools, and outputs. Marcus orchestrates; specialist subagents do the detailed work.

This section defines the target-state suite. The Minimum Viable Digital Employee in Section 11 activates only six roles first: Marcus / Orchestrator, Solicitation Analyst, Subcontractor Packet Builder, Subcontractor Outreach Coordinator, Approval Controller, and Google Drive Librarian. The remaining roles stay documented here as target-state responsibilities and are invoked as skills/checklists through the six MVDE roles until real volume justifies promoting them to active workers.

### Global subagent rules

- Internal drafting is allowed.
- External contact is not allowed without the exact approval gate.
- Do not submit, sign, certify, spend, share externally, or approve final pricing.
- Keep subcontractor-facing material separate from WFG-only internal review.
- Record source files, assumptions, risks, missing facts, and next gate.
- Use Drive links when available and local paths always.
- Create blocker notes instead of guessing when a missing fact affects price, compliance, deadline, or outreach.

### MVDE active roster versus target-state suite

- Active during MVDE: Marcus / Orchestrator, Solicitation Analyst, Subcontractor Packet Builder, Subcontractor Outreach Coordinator, Approval Controller, Google Drive Librarian.
- Target-state after MVDE: Opportunity Scout, Quote Collector, Pricing / Margin Analyst, Compliance Reviewer, Proposal Assembler, CRM / Relationship Manager, Sentinel / Risk Auditor.
- During MVDE, target-state duties are still performed, but as scoped checklists inside active roles. Example: the Approval Controller runs the Sentinel pre-outreach checklist before creating GATE_2_PACKAGE; the Outreach Coordinator performs basic CRM/contact validation before packaging recipients.
- Do not create twelve persistent workers or profiles before MVDE succeeds on a real opportunity. More named roles without volume creates routing overhead, not capability.

### Marcus / Orchestrator

- Mission: Own the opportunity pipeline, choose the next worker, explain current state to Nick, and enforce approval gates.
- Inputs: command center, opportunity records, approval queue, subagent outputs, workflow events, Nick instructions.
- Outputs: next task assignment, current state update, approval request, status summary, blocker escalation.
- Allowed actions: delegate internal tasks, create Kanban tasks, request approvals, summarize and recommend.
- Forbidden actions: external outreach, submission, price approval, certification, signing, spending, external file sharing.
- Approval gates: all gates route through Marcus or Approval Controller before Nick.
- Assigned skills/SOPs: `AGENTS.md`, `operations.md`, `approval-hub.md`, `nonbinding-draft-automation-policy.md`, `wfg-approval-coordinator`.
- Handoff triggers: new opportunity, subagent completion, approval decision, error, deadline risk, amendment, missing required input.

### Opportunity Scout

- Mission: Find and filter SAM.gov opportunities that fit WFG starter strategy.
- Inputs: `opportunity-searches/sam_search_profile.json`, SAM.gov raw batches, tracker snapshots, Google Sheet tracker output, prior seen keys.
- Outputs: morning brief, candidate list, pursue/watch/pass recommendation, opportunity intake trigger.
- Allowed actions: fetch and score opportunities, update internal tracker, create intake candidates.
- Forbidden actions: contacting agencies, submitting sources-sought responses without approval, claiming eligibility.
- Approval gates: Gate 1 if moving a candidate into live pursuit; external response approval for any agency communication.
- Assigned skills/SOPs: `wfg-sam-api-watcher`, `scripts/sam_morning_opportunity_brief.py`, `scripts/sync_sam_opportunity_tracker.py`, `sam-api-morning-agent.md`.
- Handoff triggers: strong pursue candidate, urgent deadline, sources-sought target, tracker sync failure, API/rate-limit error.

### Solicitation Analyst

- Mission: Extract solicitation facts, deadlines, scope, attachments, instructions, amendments, and source map.
- Inputs: opportunity folder, downloaded attachments, extracted text, SAM record, amendment/Q&A files.
- Outputs: solicitation brief, compliance matrix inputs, deadline table, attachment manifest, source map, missing/conflicting facts list.
- Allowed actions: parse and summarize documents, flag conflicts, recommend questions.
- Forbidden actions: asking buyer questions externally, making legal conclusions, overwriting source files without versioning.
- Approval gates: buyer Q&A approval before contacting agency; amendment reapproval when source package changes.
- Assigned skills/SOPs: `wfg-solicitation-reader`, `scripts/wfg_phase2.py`, `scripts/wfg_sub_bid_packet.py` source extraction expectations.
- Handoff triggers: analysis complete, missing critical source, amendment found, Q&A deadline approaching, source version changed.

### Subcontractor Packet Builder

- Mission: Build a clean subcontractor-facing packet and WFG-only internal review bundle.
- Inputs: solicitation brief, scope decomposition, sourcing criteria, missing information, attachment manifest, DOCX template and instructions.
- Outputs: `subcontractor_bid_packet.docx`, `subcontractor_bid_packet.md`, `internal_review_summary.md`, `source_map.json`, `bid_packet_data.json`, `review_manifest.json`, Drive bundle when enabled.
- Allowed actions: create local drafts, render DOCX, create private Drive review files.
- Forbidden actions: sending packet, exposing internal review, creating public Drive links, including WFG margin/strategy or uncertainty in the sub-facing packet.
- Approval gates: packet is reviewed as the packet component of GATE_2_PACKAGE; new package approval is required if packet version/hash changes.
- Assigned skills/SOPs: `wfg-subcontractor-bid-packet`, `scripts/wfg_sub_bid_packet.py`, `templates/subcontractor_bid_packet/`, `docs/upgrade/README_DIGITAL_EMPLOYEE_UPGRADE.md`.
- Handoff triggers: Gate 1 approved, source package changed, packet generated, missing critical packet fields, Drive upload failure.

### Subcontractor Outreach Coordinator

- Mission: Draft quote request messages and create Gate 2 outreach approval packets.
- Inputs: approved packet version/hash, recipient list, candidate evidence, contact data, internal review summary, due dates.
- Outputs: exact email/message drafts, Gmail draft IDs when configured, recipient list, approval packet, outreach manifest.
- Allowed actions: draft emails, create Gmail drafts, attach only sub-facing packet, route approval.
- Forbidden actions: sending email, using placeholder contacts, changing approved recipients after approval, attaching internal files.
- Approval gates: GATE_2_PACKAGE approves packet + recipients + message as one reviewed package; GATE_2_SEND separately approves the actual send.
- Assigned skills/SOPs: `wfg-outreach-drafter`, `wfg-approval-coordinator`, `config/email-draft-policy.md`, `scripts/wfg_email_draft_sync.py`, `scripts/wfg_gmail_drafts.py`.
- Handoff triggers: viable recipients found, draft ready, invalid recipient found, Gmail draft created, Gate 2 approved.

### Quote Collector

- Mission: Track subcontractor responses, draft follow-ups, ingest quotes, and keep status current.
- Inputs: outreach proof, Gmail threads, email response assistant classifications, received quote files, subcontractor CRM.
- Outputs: quote log, normalized quote records, follow-up drafts, no-bid records, missing info requests, response dashboard.
- Allowed actions: read inbound replies, classify responses, save quotes, create draft follow-ups, recommend next contact.
- Forbidden actions: sending follow-ups without approval, accepting quote terms as binding, promising award or exclusivity.
- Approval gates: approve quote follow-up, approve reliance on a quote or subcontractor basis-of-bid.
- Assigned skills/SOPs: `scripts/wfg_email_response_assistant.py`, `scripts/wfg_tracking_schema.py`, `scripts/wfg_phase3.py quote`, `wfg-sub-crm-manager`.
- Handoff triggers: quote received, no-bid received, clarification needed, follow-up due, quote deadline missed.

### Pricing / Margin Analyst

- Mission: Normalize quotes, identify scope gaps, calculate price options, margin, contingency, and limitations-on-subcontracting risk.
- Inputs: quote records, normalized quotes, solicitation price sheet, CLINs, compliance findings, overhead/contingency assumptions.
- Outputs: quote comparison, scope gap matrix, pricing scenarios, basis-of-bid recommendation, Gate 3 bid strategy approval packet.
- Allowed actions: calculate scenarios, recommend price strategy, flag gaps and exclusions.
- Forbidden actions: approving final price, committing to subcontractors, claiming compliance without support.
- Approval gates: approve bid strategy, approve basis-of-bid subs, approve final price/markup/contingency.
- Assigned skills/SOPs: `wfg-estimator-los`, `wfg-pricing-workbook-agent`, `wfg-los-calculator`, `scripts/wfg_phase3.py price`.
- Handoff triggers: enough quotes received, quote deadline passed, quote gap found, set-aside/LOS applies, Gate 3 packet ready.

### Compliance Reviewer

- Mission: Identify contract requirements that affect pursuit, outreach, pricing, proposal, performance, or eligibility.
- Inputs: solicitation, clauses, wage determinations, insurance/bonding requirements, set-aside data, subcontractor validation status.
- Outputs: compliance checklist, flowdown list, wage/labor summary, bonding/insurance/license/access/security summary, blocking flags.
- Allowed actions: create checklists and risk ratings, require review, recommend questions.
- Forbidden actions: legal advice as final, certifications, representations, accepting compliance risk without approval.
- Approval gates: compliance exception approval, buyer Q&A approval, final compliance signoff before proposal submission.
- Assigned skills/SOPs: `wfg-compliance-auditor`, `wfg-clause-librarian`, `wfg-cui-security-gatekeeper`, `scripts/wfg_phase3.py compliance`.
- Handoff triggers: solicitation analyzed, wage/bond/security clauses found, amendment found, proposal package ready, CUI/sensitive data detected.

### Proposal Assembler

- Mission: Assemble proposal package from approved facts, pricing, compliance artifacts, and source documents.
- Inputs: approved pricing strategy, compliance run, solicitation instructions, forms checklist, technical drafts, quote support.
- Outputs: proposal package folder, forms checklist, final compliance matrix, submission checklist, draft submission email, Gate final package approval packet.
- Allowed actions: draft and organize proposal files, map CLINs, assemble final review bundle.
- Forbidden actions: submitting, signing, certifying, inventing past performance, inserting unapproved price.
- Approval gates: approve final proposal package; approve submission separately.
- Assigned skills/SOPs: `wfg-proposal-compiler`, `wfg-submission-checklist-agent`, `scripts/wfg_phase3.py proposal`.
- Handoff triggers: Gate 3 approved, proposal package complete, required form missing, red team review complete.

### Google Drive Librarian

- Mission: Keep Drive review folders, local folders, manifests, audience labels, and links clean and synchronized.
- Inputs: opportunity folder, generated artifacts, approval records, Drive hub config, upload logs.
- Outputs: Drive folder links, review manifests, private review bundle, artifact index, broken-link/blocker report.
- Allowed actions: create private folders/files, update private Drive review docs, verify links.
- Forbidden actions: public links, external sharing, uploading sensitive files externally without explicit approval.
- Approval gates: external file sharing approval if any file is shared outside WFG; sensitive upload approval when required.
- Assigned skills/SOPs: `config/drive-review-hub.json`, `docs/upgrade/GOOGLE_DRIVE_REVIEW_HUB.md`, `scripts/wfg_sub_bid_packet.py --drive`.
- Handoff triggers: new opportunity, packet generated, approval packet created, quote received, proposal package ready, Drive upload failure.

### Approval Controller

- Mission: Maintain authorization gates, Telegram buttons, central approval folders, decision logs, DB records, and pump health.
- Inputs: approval packet requests, current workflow state, button registry, decision log, Telegram routing, workflow events.
- Outputs: approval packet, pending record, Telegram button message, reconciled decision, next-task dispatch record, pump run report.
- Allowed actions: send approval requests to Nick, reconcile decisions, queue next internal task, post factual operational status.
- Forbidden actions: approving anything, sending external outreach unless the exact downstream gate authorizes and the execution worker verifies scope.
- Approval gates: owns the mechanics of every gate but is never the approver.
- Assigned skills/SOPs: `wfg-approval-coordinator`, `approval-hub.md`, `approval-gates.md`, `scripts/send_wfg_approval_buttons.py`, `scripts/reconcile_wfg_approval_buttons.py`, `scripts/wfg_workflow_pump.py`, `scripts/wfg_approval_dispatcher.py`.
- Handoff triggers: true authorization gate reached, button clicked, approval stale, dispatch error, Telegram failure, next task queued.

### CRM / Relationship Manager

- Mission: Maintain subcontractor records, relationship history, fit notes, response patterns, and follow-up health.
- Inputs: subcontractor master CSV/MD, candidate lists, Gmail interactions, quotes, no-bids, performance notes.
- Outputs: updated subcontractor CRM records, contact quality notes, relationship status, trade/geography coverage gaps, bench-building recommendations.
- Allowed actions: update internal CRM, create research tasks, draft relationship emails.
- Forbidden actions: contacting vendors without approval, claiming WFG status/certifications, storing sensitive data in Telegram.
- Approval gates: outreach approval, follow-up approval, sensitive info request approval.
- Assigned skills/SOPs: `wfg-sub-crm-manager`, `scripts/wfg_subcontractor_crm.py`, `scripts/wfg_tracking_schema.py`, `subcontractors/subcontractor_master.csv`.
- Handoff triggers: new candidate, quote response, invalid contact, vendor no-bid, bench gap, post-award performance note.

### Sentinel / Risk Auditor

- Mission: Challenge the system before embarrassing, unsafe, duplicate, or noncompliant actions happen.
- Inputs: all approval packets, outgoing drafts, packet manifests, pricing packages, proposal packages, workflow events, external action ledger.
- Outputs: red-team report, go/no-go risk flags, duplicate-action block, source mismatch report, stale approval warning.
- Allowed actions: block or hold internal workflow pending review, create risk notes, request revisions.
- Forbidden actions: overriding human approval, making legal/final business decisions, external contact.
- Approval gates: exception approval if Nick chooses to proceed despite a material risk.
- Assigned skills/SOPs: `wfg-compliance-auditor`, `wfg-cui-security-gatekeeper`, `docs/upgrade/AGENT_DEBATE_BRIEF.md`, `scripts/wfg_phase3.py compliance`, `scripts/wfg_phase4.py`.
- Handoff triggers: pre-outreach, pre-price approval, pre-final package, duplicate target found, stale source package, CUI/sensitive data risk.

## 5. Workflow Handoff Design

### Queue concept

Use the SQLite database as the authoritative event/task queue and local files/Drive as the review record.

Agent B ruling (resolves the Kanban-versus-DB dispute): the `workflow_tasks` table is the source of truth. Kanban is an optional mirror for visibility. Dispatch must write the DB task row first, then attempt Kanban creation, and must treat Kanban CLI failure as non-fatal (log it, keep the DB task, retry the mirror later). The current dispatcher does the reverse — it depends on `hermes kanban create` succeeding (`scripts/wfg_approval_dispatcher.py:261-295`) — and must be inverted in Phase 2. A workflow that halts because a task board is down is not recoverable; a task board that lags the DB is.

Current useful tables:

- `opportunities`
- `workflow_events`
- `approvals`
- `approval_dispatches`
- `gmail_drafts`
- `subcontractor_interactions`
- `email_response_items`
- `trade_packages`
- `outreach_sends`
- `quote_records`
- `pricing_versions`
- `compliance_runs`
- `proposal_packages`

Add or standardize these tables:

```text
workflow_tasks(
  task_id,
  dedupe_key,
  opportunity_folder,
  role_id,
  task_type,
  current_state,
  priority,
  due_at,
  input_json,
  output_json,
  idempotency_key,
  created_at,
  started_at,
  finished_at,
  error,
  next_gate
)

external_action_ledger(
  action_id,
  dedupe_key,
  action_type,
  recipient_key,
  recipient_email,
  artifact_version,
  artifact_hash,
  approval_id,
  status,
  created_at,
  executed_at,
  proof_path,
  external_id,
  idempotency_key
)

artifact_index(
  artifact_id,
  dedupe_key,
  artifact_type,
  audience,
  local_path,
  drive_file_id,
  drive_web_view_link,
  version,
  sha256,
  created_at,
  superseded_at
)
```

### Task state model

Use one state model across scripts, Kanban, and dashboard:

```text
queued
claimed
running
blocked
waiting_approval
approved_to_continue
completed
failed_retryable
failed_terminal
cancelled
superseded
```

Rules:

- `queued` tasks can be claimed once by idempotency key.
- `running` tasks must heartbeat or write a progress event.
- `blocked` tasks must include a blocker note path.
- `waiting_approval` must include an approval ID and packet path.
- `approved_to_continue` is internal only and does not imply external action happened.
- `completed` must include output paths or a reason no artifact was needed.
- `superseded` must point to the newer task/artifact.

### Opportunity state model

Use the upgraded state names consistently, while mapping older script states during transition:

```text
discovered
triaged
gate1_pending_pursue
pursuing
subcontractor_packet_drafted
gate2_pending_packet_and_recipients
gate2_pending_outreach_send
outreach_approved
outreach_sent
quotes_pending
quotes_received
basis_of_bid_ready
gate3_pending_bid_strategy
proposal_in_progress
gate4_pending_final_package
gate5_pending_submission
awaiting_human_submission
submitted_by_human
submission_proof_archived
closed_no_bid
closed_lost
closed_awarded
closed_archived
amendment_review_required
blocked
```

Map older names:

- `awaiting_pursue_decision` -> `gate1_pending_pursue`
- `awaiting_outreach_approval` -> `gate2_pending_outreach_send`
- `awaiting_price_approval` -> `gate3_pending_bid_strategy`
- `awaiting_submission_approval` -> `gate4_pending_final_package` or `gate5_pending_submission`, depending on context
- `submitted` -> `submitted_by_human` only after proof exists
- `amended_reanalysis_required` -> `amendment_review_required`

Agent B ruling on migration timing: do not rewrite the enforced state machine in `scripts/wfg_phase2.py:41-56` during Phase 1. The database keeps the existing physical state names until Phase 2, when gate IDs land, a one-time migration script applies the mapping above, and the phase2 transition table is updated in the same change. Until then, dashboards and briefs may display the new names via the mapping, but no script may write them. Two live state vocabularies in the DB at once is how the system loses track of an opportunity.

Agent A Turn 2 accepts this timing and scopes the one-time migration. Agent B Turn 2 verified every count below against the extracted DB and tightened the spec to be test-precise:

- Source column: `opportunities.workflow_status` (the table has no `status` column; the migration script must use the exact column name).
- Extracted DB counts on 2026-07-07 (verified): `discovered` 23,602; `outreach_approved` 5; `awaiting_pursue_decision` 3.
- Test rows (verified): exactly two rows with `is_test_fixture=1 AND environment='test'` — one `awaiting_pursue_decision`, one `discovered`. The migration filter for all production reporting is `is_test_fixture=0 AND environment='production'`; test rows are migrated with the same mapping but never counted in dashboards or briefs.
- Production mid-pipeline rows (verified): two `awaiting_pursue_decision`, five `outreach_approved`.
- Migration mapping:
  - `discovered` -> `discovered`.
  - `awaiting_pursue_decision` -> `gate1_pending_pursue`.
  - `outreach_approved` with verified sent proof in `gmail_drafts.sent_at`, `gmail_drafts.status like '%sent%'`, outbound `subcontractor_interactions`, or sent workflow events -> `quotes_pending`.
  - `outreach_approved` without verified sent proof -> `gate2_pending_outreach_send` and create a new GATE_2_SEND packet under the new model; never execute a legacy broad Gate 2 approval automatically.
- Ledger backfill sources (verified extent as of 2026-07-07): 8 `gmail_drafts` rows with `sent_at` set (all have recipients; they span 2 opportunities), 12 outbound `email` interactions, 2 `contact_form` and 4 `form` outbound interactions, and sent-related `workflow_events` (`subcontractor_outreach_email_sent` x4, `gate2_outreach_sent` x1, `agency_q_and_a_sent` x1, `gmail_draft_sent` x2).
- Backfill dedupe rule (Agent B, required): the same physical send appears in up to three tables. Backfill must collapse sources into one ledger row per real-world action, keyed by `(opportunity dedupe_key, lower(recipient_email), sent-date)`. Each ledger row records all contributing source rows in a `sources_json` field so nothing is lost.
- Non-email contacts: `contact_form` and `form` interactions have no recipient email. Backfill keys them by `(opportunity dedupe_key, interaction_id)` with `recipient_key='form:<subcontractor_id>'` and marks them `needs_human_review` so duplicate checks still see that the company was already contacted.
- Backfill rows get `status='historical_sent_proof'`. Duplicate semantics: a `historical_sent_proof` row blocks a new send to the same `(opportunity, recipient)` pair exactly like a new-era ledger row, and any new GATE_2_PACKAGE covering a previously contacted recipient must disclose the prior contact and its date in the packet so Nick decides knowingly. Historical proof never blocks sends for a different opportunity.
- Dry-run requirement: `scripts/wfg_state_migration.py --dry-run` prints row counts, per-row proposed state, historical send proof found, and the deduped ledger backfill count. `--apply` requires a timestamped backup of `state/wfg_workflow.sqlite3` and writes a `workflow_events` record for every migrated opportunity.
- Idempotence requirement: running `--apply` twice must be a no-op the second time, proven by a test.

### Approval state model

Approvals should use:

```text
draft
pending
approved
denied
revise_requested
held
expired
superseded
used
voided
```

Approval validity rules:

- Approval must include gate ID, exact action, artifact version, artifact hash, approver, timestamp, and invalidation condition.
- Approval is invalid if the source package, recipient list, outreach text, packet, price, proposal package, or submission method changes.
- A denied/held/revise approval never dispatches the next workflow.
- Approved gates may queue internal next work.
- External execution must check the approval and the external action ledger before acting.

### Handoff map

```text
Opportunity Scout complete
  -> Solicitation Analyst task
  -> Gate 1 approval packet

Gate 1 approved
  -> Subcontractor Packet Builder
  -> CRM / Relationship Manager candidate search
  -> Google Drive Librarian review hub setup
  -> Subcontractor Outreach Coordinator draft

Packet, recipient list, and outreach draft ready
  -> Sentinel / Risk Auditor pre-outreach check
  -> GATE_2_PACKAGE approval (packet + recipients + message, one decision)

GATE_2_PACKAGE approved
  -> GATE_2_SEND approval request for the exact approved package

GATE_2_SEND approved
  -> Outreach execution worker checks ledger
  -> Send only exact approved messages
  -> Save proof
  -> Quote Collector tracking task

Quote due date or enough quotes received
  -> Quote Collector normalization
  -> Pricing / Margin Analyst
  -> Compliance Reviewer
  -> Gate 3 bid strategy approval

Gate 3 approved
  -> Proposal Assembler
  -> Sentinel / Risk Auditor
  -> Gate 4 final package approval

Gate 4 approved
  -> Gate 5 submission approval if final package approval did not explicitly include submission
  -> Human submission handoff

Human submission proof recorded
  -> Archive/closeout approval
  -> CRM updates
  -> decision log and past-performance tracker
```

### Dispatcher sequencing constraint (critical)

The current dispatcher matches gate names by substring (`scripts/wfg_approval_dispatcher.py:157-187`). Under that code, approving a gate titled "Approve Outreach Draft" would dispatch `gate2_outreach_execution` — the send path — because the title contains "Outreach". The same class of bug applies to any future gate title containing "Pursue", "Price", or "Submission".

Hard rule: no new gate names from Section 7 may appear in any approval packet until the dispatcher matches on an explicit `gate_id` field and refuses to dispatch any approval that lacks one. This is the first Phase 2 task, and it must ship with tests that prove an unknown or missing `gate_id` results in a logged refusal, not a guess.

### Retry and error handling

- Retry only internal tasks and private draft creation.
- Never retry external sends unless the external action ledger proves no send occurred.
- Every retry writes a `workflow_events` entry with retry count and previous error.
- After three retry failures, create a blocker task and route it to Marcus.
- Drive/Gmail/Telegram failures should not destroy local artifacts.
- If dispatch fails after approval, the approval remains approved but the downstream task is `failed_retryable`; the pump can resume.
- Heartbeat timeout default: a `running` task with no heartbeat or progress event for 30 minutes is marked `failed_retryable` (or `blocked` if it holds an external-action lock, which requires human review before release).
- Telegram outage: approval requests queue locally in `approvals/pending/`; the pump re-attempts button delivery on each run and posts a recovery note when delivery succeeds. An outage never voids a pending approval; only time-based expiry or artifact change does.
- Gmail token expiry or scope failure: create a credentials blocker note, mark affected tasks `blocked`, and surface the blocker in the daily brief. Never fall back to a different account or transport.
- Dead-letter rule: a task that exhausts retries and its blocker note remains unactioned for 7 days must escalate into the daily brief as a stale blocker with age, not silently rot in the queue.

### Logs

Minimum logs:

- `state/workflow-pump-runs/*.json`
- `workflow_events`
- `approval_dispatches`
- `approvals/decision-log.md`
- opportunity-local `approvals/`
- `artifact_index`
- `external_action_ledger`
- Drive review manifest
- Gmail draft/send metadata
- quote intake records
- dashboard data snapshot

### Idempotency

Use idempotency keys at each boundary:

- Approval dispatch: `wfg:<approval_id>:<dispatch_type>`
- Packet build: `subpacket:<dedupe_key>:<source_version>:<template_hash>`
- Gmail draft: `draft:<dedupe_key>:<recipient_email>:<subject_hash>:<body_hash>:<packet_hash>`
- Outreach send: `send:<approval_id>:<recipient_email>:<message_hash>:<packet_hash>`
- Quote intake: `quote:<dedupe_key>:<subcontractor_id>:<source_hash>`
- Proposal package: `proposal:<dedupe_key>:<pricing_version>:<compliance_version>:<source_version>`

### Duplicate outreach prevention

Before any outreach send:

1. Load the exact approved GATE_2_SEND approval.
2. Verify `decision = approved`, `valid = 1`, and artifact hash matches.
3. Verify the recipient is in the approved recipient list.
4. Query `external_action_ledger` for the send idempotency key.
5. Query `subcontractor_interactions` and `outreach_sends` for same opportunity, same recipient, same packet/message.
6. If a prior send exists, stop and create a duplicate-prevention note.
7. If no prior send exists, send once, record proof, update ledger, update CRM.

### Resume after interruption

The workflow pump should be safe to run every minute:

- Reconcile button decisions.
- Find approved but undispatched approvals.
- Find queued tasks without terminal status.
- Find tasks that are `running` without heartbeat past timeout and mark `failed_retryable` or `blocked`.
- Resume from the latest non-terminal state.
- Never re-execute an external action without a ledger check.

## 6. Google Drive Review Hub

Drive is the remote review hub. Telegram is the notification channel. The local repo remains the audit vault.

Agent B ruling (resolves the Drive-scope dispute): Drive mirrors review-ready artifacts only — anything Nick needs to read to make a decision, plus approval records and proof. It is not a full mirror of every intermediate file, extraction dump, or state snapshot. The local repo and SQLite stay complete; Drive stays readable on a phone. A Drive hub with 200 files per opportunity is a hub Nick stops opening.

### Root

```text
WFG Review Hub/
  SAM Opportunities/
    <YEAR>/
      <OPPORTUNITY_SLUG>/
```

Opportunity slug format:

```text
<YYYY-MM-DD>_<notice-id-or-solicitation>_<short-title>
```

Example:

```text
2026-07-07_29dbd7b0d18f_sludge-removal
```

### Folder structure

```text
WFG Review Hub/
  SAM Opportunities/
    2026/
      <OPPORTUNITY_SLUG>/
        00 Command Snapshot/
        01 Source Docs/
        02 Internal Review/
        03 Subcontractor Packet/
        04 Approvals/
        05 Draft Emails/
        06 Quotes Received/
        07 Proposal Package/
        08 Submission Proof/
        09 Pricing and Bid Strategy/
        10 Decision Logs/
```

Agent B correction: Agent A's Turn 1 tree renumbered `07 Proposal Package` to `08` and `08 Submission Proof` to `09` to slot pricing in at `07`. That collides with the live tree in `config/drive-review-hub.json`, which already defines `07 Proposal Package` and `08 Submission Proof` — existing opportunity folders would end up with two differently numbered proposal folders. Numbering is append-only: existing folders `01`-`08` keep their numbers forever; new folders take the next free numbers (`00`, `09`, `10`). Pricing at `09` is out of chronological order and that is fine — folder numbers are identifiers, not a timeline. `config/drive-review-hub.json` must be updated to this exact tree in Phase 3, and folder creation must be idempotent by name (find-or-create, never create-duplicate).

### Naming conventions

Use:

```text
<YYYYMMDD>_<artifact_type>_<version>_<short-title>.<ext>
```

Examples:

```text
20260707_internal_review_subpacket-a1b2c3d4_sludge-removal.md
20260707_subcontractor_packet_subpacket-a1b2c3d4_sludge-removal.docx
20260707_gate2_outreach_approval_appr_1234abcd.md
20260707_quote_vendor-name_quote-5678.pdf
20260707_final_package_proposal-abcd1234.zip
```

### What goes where

- Source docs: `01 Source Docs/`
  - SAM.gov export
  - solicitation/RFQ
  - amendments
  - PWS/SOW
  - drawings/specs
  - wage determinations
  - price sheets
  - Q&A
  - site visit notes

- Internal review summary: `02 Internal Review/`
  - solicitation brief
  - compliance matrix
  - risk register
  - missing information
  - source map
  - internal bid/no-bid notes
  - internal packet review

- Subcontractor packet: `03 Subcontractor Packet/`
  - generated subcontractor-facing DOCX
  - generated subcontractor-facing Markdown backup
  - never internal review files

- Draft emails: `05 Draft Emails/`
  - local draft copies
  - Gmail draft IDs and links
  - recipient list
  - message version and hash

- Approval records: `04 Approvals/`
  - approval packet copies
  - Telegram message metadata
  - approval decision records
  - exact action, version, hash, and invalidation condition

- Received quotes: `06 Quotes Received/`
  - quote PDFs/emails
  - quote normalization JSON/CSV
  - exclusions and clarifications
  - quote comparison

- Pricing and bid strategy: `09 Pricing and Bid Strategy/`
  - quote comparison
  - pricing scenarios
  - basis-of-bid recommendation
  - GATE_3_STRATEGY approval packet copy

- Final proposal package: `07 Proposal Package/`
  - technical proposal
  - pricing schedule
  - required forms
  - compliance matrix
  - red team review
  - final review checklist

- Submission proof: `08 Submission Proof/`
  - portal confirmation
  - sent email proof
  - timestamp screenshot
  - final submitted package hash

- Decision logs: `10 Decision Logs/`
  - bid/no-bid decision
  - approval history
  - archive/closeout decision
  - win/loss/debrief notes

- Command snapshot: `00 Command Snapshot/`
  - one-page mobile summary named `command_snapshot.md` and refreshed after each major state change
  - current workflow state and next gate
  - one-line recommendation from Marcus
  - waiting approvals with age and exact action
  - key dates: government due, Q&A, site visit, sub quote due, follow-up due
  - review links: packet, internal review summary, draft email/Gmail draft, approval packet, quote comparison, proposal package when present
  - artifact versions/hashes for anything awaiting approval
  - open blockers and owner
  - last five workflow events
  - explicit no-send/no-submission status until proof exists
  - a `generated_at` timestamp and the workflow state at generation time, so a stale snapshot is self-evident on a phone
  - single-file rule: `command_snapshot.md` is overwritten in place — one file per opportunity, never accumulating dated copies; history lives in `workflow_events`, not in Drive

### Drive safety

- Private by default.
- No public links.
- No external sharing from packet or Drive hub scripts.
- External sharing requires exact approval of file, recipient, purpose, version, and hash.
- Drive upload failure creates a blocker but does not stop local artifact creation.

## 7. Approval System

Approvals should be gate-specific, exact, version-bound, and recoverable.

### Approval gates

Rules that apply to every gate:

- Every gate carries a machine-readable `gate_id`, listed in parentheses below. The dispatcher must match on `gate_id` only — never on gate-name substrings (see Section 5 sequencing constraint).
- Default expiry: an approval expires 14 days after grant, immediately when an amendment changes the source package, and immediately when any referenced artifact hash changes. Expired approvals never dispatch.
- Decision vocabulary is exactly: `approved`, `denied`, `revise_requested`, `held`. The reconciler currently writes `rejected`; Phase 2 standardizes it to `denied` with a migration of existing rows.
- Telegram buttons must grow to four: Approve / Deny / Revise / Hold. Until the Revise and Hold buttons exist, an exact-text reply (`revise: <instruction>` or `hold`) is the fallback; emoji, silence, and "looks good" are never decisions.

Agent A Turn 2 accepts Agent B's Gate 2 restructure. Agent A's Turn 1 draft split Gate 2 into four sequential approvals (packet, list, draft, send). That is safer on paper and weaker in practice: Nick approves from a phone, and four taps per opportunity trains rubber-stamping, which defeats the gate. The consensus model below uses two decisions: one package approval covering all three components reviewed together, then a separate send approval. Component-level revision is preserved without component-level gating.

#### Gate 1 - Approve Opportunity Pursuit (GATE_1_PURSUE)

- Exact question: Should WFG move this opportunity into active pursuit and continue internal preparation?
- Required review: intake, bid/no-bid scorecard, solicitation brief, risk summary, deadline summary.
- Allows: internal sourcing, packet building, draft outreach, Drive review setup.
- Does not allow: contacting anyone, submitting anything, final pricing, external sharing.

#### Gate 2 - Approve Outreach Package (GATE_2_PACKAGE)

- Exact question: Are this exact packet (hash), these exact recipients, and this exact message text approved together as the outreach package for this opportunity?
- Required review, presented as three labeled components in one packet:
  - Packet: DOCX/MD, internal review summary, source map, missing critical items, packet version/hash.
  - Recipients: candidate list, contact source evidence, email validity, scope fit, rejection reasons, duplicate-history check.
  - Message: exact subject, body, attachments list, message hash, Gmail draft link and Drive draft copy.
- Component revision rule: Nick may respond `revise: recipients` (or `revise: packet`, `revise: message`) with instructions. Only the named component is reworked; the package is re-submitted as a new version with unchanged component hashes carried forward and marked "unchanged since v(N)", so re-review takes seconds.
- Allows: the package to be queued for GATE_2_SEND. Nothing else.
- Does not allow: sending anything to anyone.

#### Gate 2-SEND - Approve Sending Outreach (GATE_2_SEND)

- Exact question: May Hermes send the exact approved package (message hash + packet hash) to the exact approved recipients now?
- Required review: GATE_2_PACKAGE approval ID, recipient list, message hash, packet hash, send method, external action ledger duplicate check.
- Allows: one send per approved recipient after ledger check.
- Does not allow: changed recipients, changed message, changed packet, follow-ups, promises, commitments.
- The GATE_2_SEND request may be presented immediately after GATE_2_PACKAGE approval as a second button in the same Telegram thread, but it must be a distinct recorded decision with its own approval ID. Two taps total per outreach round is the design target.

#### Gate 3A - Approve Quote Follow-Up (GATE_3_FOLLOWUP)

- Exact question: May Hermes send this follow-up or clarification request to these exact subcontractors?
- Required review: follow-up text, recipient list, reason, original outreach proof, due date.
- Allows: exact follow-up send after ledger check.
- Does not allow: scope change, commitment, final price, or agency communication.

#### Gate 3B - Approve Bid Strategy (GATE_3_STRATEGY)

- Exact question: Should WFG proceed with this basis-of-bid strategy, selected subcontractors, assumptions, exclusions, and price direction?
- Required review: quote comparison, scope gaps, compliance flags, LOS check, margin/contingency assumptions.
- Allows: proposal assembly using approved pricing basis.
- Does not allow: final submission.

#### Gate 4 - Approve Final Proposal Package (GATE_4_PACKAGE)

- Exact question: Is this exact proposal package approved as final for submission preparation?
- Required review: proposal package, required forms, compliance matrix, pricing schedule, red-team review, amendment check.
- Allows: human submission handoff or separate submission approval packet.
- Does not allow: automated submission unless a future separate mechanism is explicitly approved and safe. Current rule: human submits.

#### Gate 5 - Approve Submission (GATE_5_SUBMIT)

- Exact question: May the authorized human submit this exact package by this exact method?
- Required review: final package hash, deadline, submission method, portal/email instructions, certifications/signature requirements.
- Allows: human submission and proof archive.
- Does not allow: Hermes submitting automatically under the current operating model.
- Elevated confirmation: GATE_5_SUBMIT is not a one-tap button. The approval requires an exact-text reply that includes the first 8 characters of the final package hash (for example `APPROVE GATE_5 a1b2c3d4`). This proves Nick opened the actual package, not just the notification. Gates 1-4 remain one-tap.

#### Gate 6 - Approve Archive/Closeout (GATE_6_CLOSE)

- Exact question: Should this opportunity be archived as no-bid, submitted, lost, awarded, cancelled, or held?
- Required review: final state, proof records, open follow-ups, CRM updates, lessons learned.
- Allows: closeout, dashboard archive, CRM update, past-performance/debrief record if applicable.
- Does not allow: deleting audit records.

#### Gate A - Approve Continuing Under Amendment (GATE_AMEND_CONTINUE)

Added by Agent B: Agent A's draft invalidated approvals on amendment but had no gate to resume work, leaving `amendment_review_required` a dead end.

- Exact question: Having reviewed amendment N, should this opportunity continue from its prior stage, restart analysis, or close?
- Required review: amendment summary with what changed, affected artifacts and voided approvals, deadline changes, impact on packet/recipients/pricing if already in flight.
- Allows: resuming internal work from the stage Nick selects; re-issuing voided gates with new versions.
- Does not allow: reusing any approval voided by the amendment; any external action.

### Approval packet required fields

Use the current `approvals/templates/approval-packet-template.md` as the base, with these required fields:

- Approval ID
- Gate ID and gate name
- Opportunity/project name
- Notice ID
- Solicitation number
- Opportunity folder
- Current workflow state
- Requested by agent/subagent
- Exact action requiring authorization
- Exact item being approved
- Artifact/package version
- Artifact hash
- Recipient list, when applicable
- Price/basis-of-bid, when applicable
- Files Nick should review
- Drive links, when available
- Gmail draft ID/link, when applicable
- Risks
- Missing information
- Assumptions requiring confirmation
- Invalidation condition
- Recommended decision
- Approval log location

### Approver identity and Managing Member evidence

The reconciler already records `telegram_user_id` per decision (`scripts/reconcile_wfg_approval_buttons.py:157`). Build on that:

- Add `config/approvers.json` mapping Telegram user IDs to named humans and their authority (for now: Nick, Managing Member, all gates). Only listed IDs count as decisions; a button press from any other ID is logged and ignored with an alert.
- For binding decisions (GATE_3_STRATEGY, GATE_4_PACKAGE, GATE_5_SUBMIT), the evidence of Managing Member approval is: the decision row with the approver's Telegram ID, the `approvals/decision-log.md` entry, and a copy of the decided approval packet in Drive `04 Approvals/`. That trio is the audit answer to "who authorized this" — no separate signature ceremony is needed at this stage.

### Mobile approval flow

Nick must be able to run a full approval cycle from a phone. The concrete loop:

1. Telegram notification arrives with gate, opportunity, a one-line recommendation, and buttons (Approve / Deny / Revise / Hold).
2. The message includes Drive links to the exact files under review, openable in the Drive mobile app, and the Gmail draft link when the gate involves outreach text.
3. Nick reviews in Drive/Gmail, returns to Telegram, taps a button — or replies `revise: <instruction>` / `hold` as exact-text fallback.
4. The pump reconciles the decision within one scheduled run and posts the factual follow-up to the routed operational topic.
5. GATE_5_SUBMIT alone requires the hash-confirmation reply described above.
6. The daily brief lists every waiting approval with its age, so nothing waits silently.

If any step in this loop requires a laptop, that is a bug against this plan.

## 8. Subcontractor Bid Packet Integration

The current bid packet system should become the standard post-Gate-1 packet path.

### Research-first barrier (amendment, 2026-07-07)

Added after live operation showed Hermes generating placeholder packets by running the renderer before doing the research. The rule and its enforcement:

- Rule: **Research first. Packet second. Outreach third. Approval before external action always.** A subcontractor bid packet is not a research tool; it is the output of completed research. A research blocker is a correct output; a placeholder packet is a failure.
- `scripts/wfg_research_preflight.py` is the internal quality gate (not a human approval gate). It dry-runs the packet builder's own extraction and FAILS when any required fact would fall back to placeholder output (missing title/agency/deadline/location, generic scope, scaffold markers left in research artifacts, empty `source/`). It writes `research_preflight.json` (with artifact hashes) and, on FAIL, `research_blocker.md` with numbered fix instructions and the exact labeled-line format `02_SOLICITATION_BRIEF.md` must use.
- `scripts/wfg_sub_bid_packet.py` refuses to render unless the preflight marker says PASS **and** the artifact hashes still match (a PASS goes stale when research files change). `--allow-incomplete-draft` exists only for internal test renders and must never feed a gate or a subcontractor.
- `scripts/wfg_outreach_cycle.py build-package` refuses to create a GATE_2_PACKAGE without a current PASS. There is no bypass flag on that path.
- Placeholder boundary: scaffold markers (`[USER INPUT REQUIRED]`, `[DOCUMENT MISSING]`, …) are allowed only in initial intake scaffolds and internal analysis drafts. They are forbidden in `02_SOLICITATION_BRIEF.md`, `05_SCOPE_DECOMPOSITION.md`, `06_SUBCONTRACTOR_SOURCING_CRITERIA.md`, and `attachment_manifest.md`; unknown facts go in `04_MISSING_INFORMATION.md` with the documents checked. This boundary is also written into `AGENTS.md`, `MARCUS.md`, and `nonbinding-draft-automation-policy.md`.
- The Gate 1 dispatcher task is an explicit two-phase checklist (Phase A research with a hard STOP on preflight FAIL; Phase B packet/recipients/message only after PASS), and the pipeline order is codified in `AGENTS.md` ("Opportunity pipeline order (hard rule)").
- Tests: `tests/test_research_preflight.py` proves empty/scaffold/missing-deadline/generic-scope research fails, complete research passes, a changed artifact stales the PASS, the builder refuses without PASS, and GATE_2_PACKAGE cannot form on incomplete research.

### Authoritative files

```text
templates/subcontractor_bid_packet/WFG_Subcontractor_Bid_Packet_Template.docx
templates/subcontractor_bid_packet/Hermes_Subcontractor_Bid_Packet_Instructions.docx
templates/subcontractor_bid_packet/README.md
scripts/wfg_research_preflight.py
scripts/wfg_sub_bid_packet.py
/Users/nickwright87/WFG/wfg_upgraded/.hermes/skills/business-ops/wfg-subcontractor-bid-packet/SKILL.md
```

### Source extraction

Input sources should be read in this priority order:

1. Amendments and Q&A.
2. Solicitation/RFQ and PWS/SOW.
3. Price schedule, CLINs, bid sheets, drawings/specs.
4. Wage determinations.
5. Site visit notices and minutes.
6. Safety, access, license, bonding, insurance, disposal, and security requirements.
7. Extracted text from attachments.
8. SAM.gov metadata only when solicitation documents do not provide the fact.

Every extracted fact used in the packet needs a source map entry.

### Relevance filtering

Only include facts that help a subcontractor decide fit and price:

- project title
- agency
- solicitation/RFQ number
- notice ID
- location
- quote deadline
- government deadline
- Q&A date
- site visit date
- performance period
- brief scope
- work items
- price sheet/CLIN/unit/quantity details
- attachments needed for pricing
- wage/labor requirements when applicable
- bonding/insurance/license/access/security/safety requirements when they affect pricing or eligibility
- disposal, staging, utilities, schedule, cleanup, and special conditions when relevant

Do not include:

- WFG margin, markup, target profit, bid/no-bid score, or price strategy
- internal uncertainty
- AI disclaimers
- missing-info warnings
- unrelated SAM.gov metadata
- full clause dumps unless they affect the sub's price/scope
- government forms unless a sub must complete or understand a specific part
- embarrassing phrasing like "unknown", "maybe", "not sure", or "[DOCUMENT MISSING]"

### Clean subcontractor-facing output

The packet should look like a professional quote request package:

- short fit check
- clear scope
- clear line items
- clear due dates
- clear instructions
- only relevant attachments
- clean language
- no internal notes
- no unresolved placeholders

### Internal review summary

All uncertainty belongs in:

```text
subcontractor_bid_packet/internal_review_summary.md
```

That file should include:

- packet readiness
- missing critical items
- conflicts
- source files used
- recommended human checks
- risks before outreach
- Gate 2 reminder

### Source map

Use:

```text
subcontractor_bid_packet/source_map.json
```

Each major field should include:

- source file
- extraction method
- confidence
- source version/hash when available

### Missing data handling

- If a missing fact affects pricing, compliance, deadline, site visit, scope, recipient choice, or outreach, mark the packet `Needs human review`.
- Do not expose missing facts in the sub-facing packet.
- Use "See solicitation package" only when the packet still gives enough context and the attached source is available to the subcontractor.
- If a critical missing fact makes outreach risky, create a blocker note instead of Gate 2 send approval.

### Price sheets, deadlines, site visits, Q&A, scope, attachments, wage/license/bonding/safety

Only include these when relevant and supported:

- Price sheets: include CLINs, units, quantities, options, alternates, and whether to price separately.
- Deadlines: include sub quote due and government response due. Sub quote due should default to two business days before government due unless Nick sets another date.
- Site visits: include required/optional, date/time, location, registration, and whether the date has passed.
- Q&A: include deadline and instruction to route questions to WFG for consolidation.
- Scope: include trade-specific work items and clear exclusions.
- Attachments: list only those a subcontractor should review and why.
- Wage/license/bonding/safety: include if they affect labor cost, eligibility, documentation, or performance.

## 9. Draft Outreach System

### Drafting

The Outreach Coordinator drafts outreach after:

1. Gate 1 pursuit is approved.
2. Candidate list is created.
3. Bid packet is generated.
4. Internal review summary is available.
5. Sentinel pre-outreach review finds no blocker.

Gmail draft timing rule: create Gmail drafts only after the packet exists, recipients validate, and the draft message has a local source file. Pre-approval Gmail drafts must include `[PENDING WFG GATE 2]` at the start of the subject. The approved send worker later composes from the approved body text and verifies the approved message hash; it does not blindly send whatever happens to be in Gmail Drafts.

The draft should include:

- subject
- recipient company and email
- exact body text
- attachments
- packet version/hash
- due date
- requested quote contents
- no promise of award
- no claims about WFG certifications unless verified
- no internal review attachments

### Storage

Store drafts in:

```text
<opportunity>/05 Draft Emails/
<opportunity>/drafts/
<opportunity>/approvals/
```

Also mirror review copies to:

```text
Drive: 05 Draft Emails/
Gmail Drafts tab
state/wfg_workflow.sqlite3:gmail_drafts
```

### Review

Nick should be able to review:

- local file path
- Drive link
- Gmail draft
- recipient list
- packet link/path
- internal review summary
- approval packet

### Approval

There are exactly two decisions (see Section 7):

- GATE_2_PACKAGE: one approval of packet + recipients + message, reviewed as three labeled components in one packet with all versions and hashes.
- GATE_2_SEND: a separate recorded decision to send the exact approved package.

A `revise:` response reworks only the named component and re-submits the package as a new version with unchanged hashes carried forward.

### Sending

No direct send without approval.

Send execution steps:

1. Load GATE_2_SEND approval.
2. Verify exact recipient/message/packet.
3. Verify no duplicate send in ledger/interactions.
4. Send one message per approved recipient.
5. Record Gmail sent metadata or form confirmation.
6. Save proof path.
7. Update `subcontractor_interactions`.
8. Schedule quote follow-up as draft task, not automatic send.

## 10. Business Dashboard / Command Center

Nick should see one daily operations view, not scattered files.

### Daily command center sections

- Active opportunities:
  - title
  - agency
  - solicitation
  - due date
  - stage
  - next action
  - owner subagent
  - risk level

- Waiting approvals:
  - gate
  - age
  - exact decision needed
  - Drive links
  - Telegram approval status
  - stale/superseded warning

- Due dates:
  - government deadline
  - Q&A deadline
  - site visit
  - sub quote deadline
  - follow-up deadline
  - proposal review date

- Subcontractor responses:
  - sent count
  - opened/replied if available
  - quotes received
  - no-bids
  - follow-ups due
  - invalid contacts

- Missing info:
  - documents
  - pricing
  - compliance
  - recipient
  - approval
  - Drive/Gmail/Telegram setup

- Risks:
  - source amendment
  - stale approvals
  - missing wage determination
  - set-aside/LOS concern
  - no viable subs
  - deadline compression
  - duplicate outreach blocked

- Next recommended actions:
  - approve/revise/hold
  - ask buyer Q&A
  - request more subs
  - no-bid
  - proceed to proposal
  - archive

- Money/pipeline view:
  - estimated opportunity value
  - quote total
  - proposed markup/contingency
  - gross margin estimate
  - probability/stage
  - expected submission date

### Dashboard files to evolve

- `scripts/wfg_static_dashboard.py`
- `cloudflare-dashboard/`
- `obsidian-vault/00-Dashboards/WFG Command Center.md`
- `obsidian-vault/00-Dashboards/Opportunity Pipeline.md`
- `scripts/wfg_obsidian_sync.py`

### Acceptance target

Nick should be able to open Telegram or Drive and answer:

1. What is Hermes working on?
2. What needs my approval?
3. What is due soon?
4. Which subcontractors have responded?
5. What is blocked?
6. What should I do next?

## 11. Implementation Roadmap

### Minimum Viable Digital Employee (MVDE)

Agent A Turn 2 accepts Agent B's MVDE framing. The eight phases below are correct in content but too broad to build at once for the current stage: one operator, low deal volume, and no currently callable repo script that sends email. Historical sent-proof records exist in the extracted database and must be preserved/backfilled, but the next implementation should still treat send execution as new code. The MVDE is the smallest system that lets Nick run one opportunity end-to-end from his phone with full safety. Build it first; everything else is deferred until it has run a real opportunity.

MVDE scope:

1. Phase 1 complete: paths unified, `config/subagents.json` skill names fixed, cron verified against v2, replacement instructions consolidated.
2. Phase 2 complete: gate IDs, `workflow_tasks`, `external_action_ledger`, `artifact_index`, four-button decisions, duplicate-send block, dispatcher/pump tests.
3. Phase 3 subset: Drive upload of the packet review bundle, approval packet copies, and a command snapshot doc — not the full tree automation for every artifact type.
4. Phase 5 subset: Gmail drafts plus the GATE_2_PACKAGE / GATE_2_SEND cycle. The send worker is written last, only after ledger tests pass in Phase 2.
5. Phase 7 subset: a daily Telegram brief listing stages, waiting approvals with age, and deadlines. Text only; no published dashboard.

Explicitly deferred beyond MVDE: full Drive tree automation, pricing/proposal automation (Gates 3B through 6 run manually with templates), the money/pipeline view, CRM enhancements, Cloudflare dashboard publication, and any `wfg_phase*` refactor. Consensus position: wrap existing `wfg_phase*` scripts behind clearer commands during MVDE; do not refactor them until MVDE has proven the operating loop.

MVDE subagent roster — six roles, not twelve: Marcus (orchestrator), Solicitation Analyst, Subcontractor Packet Builder, Outreach Coordinator, Approval Controller, Drive Librarian. Scout, Quote Collector, Pricing Analyst, Compliance Reviewer, Proposal Assembler, CRM Manager, and Sentinel remain in Section 4 as target-state roles; until volume justifies promotion, their duties run as skills/checklists invoked by the six MVDE roles (Sentinel's pre-outreach checklist runs inside the Approval Controller's packet build, for example). A role registry with twelve idle workers is bookkeeping, not capability.

Definition of done for MVDE: one real opportunity goes intake -> GATE_1_PURSUE -> packet -> GATE_2_PACKAGE -> GATE_2_SEND -> send with proof -> quote responses logged, operated entirely from Telegram + Drive + Gmail on a phone, with every step visible in `workflow_events` and a test proving a duplicate send is impossible.

### Phase ordering rules

- Phase 2 must complete before any new gate names appear in approval packets (Section 5 sequencing constraint).
- The `external_action_ledger` and its tests must merge before any send connector code is written.
- Phases 4, 6, 7, and 8 must not begin before the MVDE definition of done is met, except for the Phase 7 daily-brief subset named above.

### Phase 1 - Stabilize current repo

Files likely affected:

- `README.md`
- `AGENTS.md`
- `operations.md`
- `agent-team.md`
- `docs/upgrade/*`
- `config/subagents.json`
- `scripts/wfg_phase1.py`
- `scripts/wfg_phase2.py`
- `scripts/wfg_phase3.py`
- `scripts/wfg_workflow_pump.py`
- `.hermes/scripts/*`
- `.hermes/cron/jobs.json`

Build/change:

- Decide whether this extracted folder becomes the Git repo or is merged into the live repo.
- Standardize path defaults to `/home/nick/workspace/wfg-gov-contracting-v2` with `WFG_PROJECT_DIR` override. Exact files (verified): `scripts/sam_brief_deliver.py`, `scripts/sam_raw_fetch.py`, `scripts/sam_tracker_snapshot.py`, `scripts/sam_tracker_sync.py`, `scripts/run_test_intake_selected.py` (all hardcoded, no env override today), plus wrong defaults in `scripts/sam_morning_opportunity_brief.py`, `scripts/sync_sam_opportunity_tracker.py`, `scripts/wfg_phase1.py`.
- Align `config/subagents.json` skill names with installed skills using the exact 8-entry mapping in Section 2. Rename config entries; do not create alias skill folders.
- Confirm `.hermes` cron scripts point at the upgraded workspace files.
- Confirm Gmail draft sync uses the placeholder-recipient-safe version.
- Add a `docs/strategy/` index that points Hermes to this plan.
- Reduce `docs/upgrade/HERMES_REPLACEMENT_INSTRUCTIONS.md` to a pointer stub referencing Section 12 of this plan, and mark `docs/upgrade/AGENT_DEBATE_BRIEF.md` as historical.
- Add `config/approvers.json` (Telegram user ID -> named approver mapping, per Section 7).

Acceptance criteria:

- `python3 -m py_compile` passes for core scripts.
- Existing tests pass or failures are documented.
- `python3 scripts/wfg_workflow_pump.py --no-kanban-dispatch` runs safely.
- `.hermes/scripts/wfg_approval_reconciler.sh` calls the pump.
- No script defaults to the old `gov-contracting` path unless explicitly compatibility-wrapped.

Risks:

- Live `.hermes` copy may not match workspace files.
- Changing paths can break cron jobs.
- The extracted folder has no `.git`, so GitHub push workflow needs a clear source of truth.

### Phase 2 - Approval queue and workflow handoff

Files likely affected:

- `scripts/wfg_workflow_pump.py`
- `scripts/wfg_approval_dispatcher.py`
- `scripts/reconcile_wfg_approval_buttons.py`
- `scripts/wfg_phase2.py`
- `scripts/wfg_tracking_schema.py`
- `approvals/templates/approval-packet-template.md`
- `config/approval-routing.json`
- tests under `tests/`

Build/change:

- Add explicit gate IDs and remove gate-name string matching from `scripts/wfg_approval_dispatcher.py`. This is the first task of the phase; no new gate names ship before it (Section 5 sequencing constraint).
- Add `workflow_tasks`, `external_action_ledger`, and `artifact_index`.
- Standardize task and approval state models, including migrating existing `rejected` decisions to `denied` and applying the opportunity-state mapping with a one-time migration script that also updates the `scripts/wfg_phase2.py` transition table.
- Build `scripts/wfg_state_migration.py` with the exact dry-run/apply behavior and row mapping in Section 5.
- Backfill `external_action_ledger` from historical sent proof before enabling any new send worker.
- Invert dispatch order: write the DB task row first, mirror to Kanban second, tolerate Kanban failure (Section 5 ruling).
- Add Revise and Hold buttons to `scripts/send_wfg_approval_buttons.py` and reconciler handling for `revise_requested` and `held`, with exact-text fallback.
- Make approval dispatch idempotent and resumable.
- Add duplicate outreach block before any send.
- Write blocker notes for failed dispatch.
- Add `tests/test_approval_dispatcher.py` and `tests/test_workflow_pump.py`.

Acceptance criteria:

- Approving GATE_1_PURSUE queues exactly one next internal task.
- The dispatcher refuses (with a logged refusal event) any approval that lacks a known `gate_id`.
- A GATE_2_SEND approval cannot produce two sends to the same recipient with the same packet/message — proven by a test against the ledger, not by inspection.
- Denied/held/revise decisions do not dispatch next work — proven by a test.
- Superseded artifact invalidates affected approvals.
- Pump run logs show each step and error.
- Kanban CLI failure during dispatch leaves a valid `workflow_tasks` row and a retryable mirror step, not a lost task.
- `scripts/wfg_state_migration.py --dry-run` reports exactly the verified counts from Section 5 without writing: 23,601 production `discovered`, two production `awaiting_pursue_decision`, five production `outreach_approved`, plus two test fixture rows excluded from production dashboard/brief counts.
- `scripts/wfg_state_migration.py --apply` creates a timestamped DB backup, records migration events, and is idempotent on a second run.
- Deduped historical send/contact backfill produces one `external_action_ledger` row per real-world contact, populates `sources_json`, marks form contacts without recipient email as `needs_human_review`, and runs before Phase 5 send execution is enabled.
- A duplicate-send test proves a `historical_sent_proof` ledger row blocks a new send to the same opportunity/recipient pair but does not block the same recipient for a different opportunity.

Risks:

- Existing approval records may use inconsistent gate text.
- Telegram button registry and DB may drift.
- Kanban CLI failure could block dispatch unless fallback task records exist.

### Phase 3 - Google Drive review hub

Files likely affected:

- `config/drive-review-hub.json`
- `docs/upgrade/GOOGLE_DRIVE_REVIEW_HUB.md`
- `scripts/wfg_sub_bid_packet.py`
- new `scripts/wfg_drive_review_hub.py`
- `scripts/wfg_static_dashboard.py`

Build/change:

- MVDE first: create the opportunity Drive folder, `00 Command Snapshot/`, `03 Subcontractor Packet/`, `04 Approvals/`, and `05 Draft Emails/`; upload only review-ready packet, approval, draft-email, and command-snapshot artifacts.
- After MVDE: create and synchronize the full review-ready tree in Section 6. Do not mirror every extraction dump or intermediate state file.
- Write Drive links to `artifact_index` and opportunity manifests.
- Verify private-only permissions.
- Add Drive upload failure blocker handling.

Acceptance criteria:

- MVDE: a pursued opportunity gets the command snapshot, packet, approvals, and draft-email review folders with private links in `artifact_index`.
- Full Phase 3 after MVDE: a pursued opportunity gets the complete append-only folder tree in Section 6.
- Every review artifact has local path, Drive link, version, hash, and audience label.
- No public links are created.
- Drive failure does not prevent local artifact creation.

Risks:

- Google token scope/refresh issues.
- Sensitive files could be uploaded without classification if CUI gate is weak.
- Drive folder duplicates if slug rules are not idempotent.

### Phase 4 - Subagent role system

Deferred until after MVDE except for the six active role templates in Section 11.

Files likely affected:

- `agents/WFG_SUBAGENT_PROFILES.md`
- `config/subagents.json`
- `agent-team.md`
- `operations.md`
- new `scripts/wfg_delegate_task.py` or equivalent wrapper
- `.hermes/kanban/` configuration

Build/change:

- Convert this plan's agent suite into executable subagent task templates.
- Add role IDs, missions, allowed/forbidden actions, outputs, and gate triggers to `config/subagents.json`.
- Make Marcus create durable Kanban/delegated tasks with role, input folder, outputs, skills, and approval boundaries.
- Add Sentinel review before outreach, pricing, and final package.

Acceptance criteria:

- Marcus can queue each subagent type from one standard command/template.
- Each subagent output states role, source files, assumptions, risks, and next gate.
- No "skill only" task is treated as a worker.

Risks:

- Too many role names could confuse Hermes if config and docs disagree.
- Persistent separate profiles may not be necessary yet; start with task profiles.

### Phase 5 - Outreach and quote collection

Files likely affected:

- `scripts/wfg_email_draft_sync.py`
- `scripts/wfg_gmail_drafts.py`
- `scripts/wfg_email_response_assistant.py`
- `scripts/wfg_tracking_schema.py`
- `scripts/wfg_subcontractor_crm.py`
- `scripts/wfg_phase3.py`
- `config/email-draft-policy.md`
- `config/email-response-assistant-policy.md`

Build/change:

- Require GATE_2_SEND before send.
- Build order rule: no send connector code is written until the `external_action_ledger` and its duplicate-block tests are merged (Phase 2). No send capability exists in the repo today (verified); the first send code written must be the worker that checks the ledger.
- Add send execution worker with ledger checks. The worker composes the final message from the approved body text and verifies its hash against the approval; it does not blindly fire whatever sits in the Gmail draft.
- Gmail drafts created before approval must carry a `[PENDING WFG GATE 2]` subject prefix so a human cannot mistake one for a ready-to-send message; the send worker composes the real message without the prefix.
- Record sent proof in `external_action_ledger`, `subcontractor_interactions`, and opportunity folder.
- Ingest replies and quote files.
- Draft follow-ups but require GATE_3_FOLLOWUP before sending.
- Update CRM with response/no-bid/quote status.

Acceptance criteria:

- Placeholder emails are skipped and logged.
- Gmail drafts are verified and recorded.
- Approved outreach sends once and logs proof.
- Follow-up drafts are created but not sent without approval.
- Quote responses show on dashboard.

Risks:

- Gmail API send behavior must be tested carefully.
- Contact forms need a separate approved workflow and proof handling.
- Subcontractor replies may be hard to match without stable thread IDs.

### Phase 6 - Proposal assembly

Files likely affected:

- `scripts/wfg_phase3.py`
- `templates/`
- `docs/upgrade/DIGITAL_EMPLOYEE_OPERATING_MODEL.md`
- `approval-gates.md`
- `approvals/templates/approval-packet-template.md`

Build/change:

- Formalize pricing package, compliance package, and proposal package artifacts.
- Create Gate 3B bid strategy packet.
- Create Gate 4 final package packet.
- Create Gate 5 human submission packet/checklist.
- Make proposal package hash version-bound.

Acceptance criteria:

- Proposal package contains pricing, technical, forms, compliance, red-team review, and submission checklist.
- Final package approval does not imply submission unless Gate 5 explicitly says so.
- Submission proof is required before state becomes `submitted_by_human`.

Risks:

- Forms and portal-specific submission instructions vary by solicitation.
- Compliance/legal review may block fast turnaround.

### Phase 7 - Dashboard and business command center

MVDE subset: daily Telegram command brief only. Published dashboard work waits until authentication is selected.

Files likely affected:

- `scripts/wfg_static_dashboard.py`
- `cloudflare-dashboard/index.html`
- `scripts/wfg_obsidian_sync.py`
- `obsidian-vault/00-Dashboards/*.md`
- new `scripts/wfg_command_center.py`

Build/change:

- Add waiting approvals, due dates, risk flags, next actions, and money/pipeline view.
- Pull from workflow DB, approval queue, Drive manifests, Gmail drafts, and interactions.
- Generate a daily dashboard snapshot and Telegram summary.
- Keep mobile review clean and low-noise.

Acceptance criteria:

- Dashboard answers the six command-center questions listed in Section 10.
- Waiting approval list links to Drive/local artifacts.
- Risks and blockers are visible without IDE access.
- Static dashboard can be published/reviewed without exposing secrets.

Risks:

- Dashboard could expose sensitive info if published without filtering.
- Cloudflare/static output needs auth or private access before business use.

### Phase 8 - Hardening and GitHub review process

Files likely affected:

- `tests/`
- `.github/workflows/` if GitHub is initialized
- `README.md`
- `README_V2_UPGRADE.md`
- `VERSION.md`
- `docs/strategy/`
- `.env.example`
- deployment scripts

Build/change:

- Add regression tests for approvals, packet safety, duplicate outreach, Drive private-only logic, and placeholder recipient blocking.
- Add py_compile and pytest CI.
- Add no-secret checks.
- Add deployment instructions for `.hermes` scripts and live cron.
- Add GitHub PR checklist.

Acceptance criteria:

- Core tests run before push.
- No secrets committed.
- A fresh clone has setup instructions.
- Deployment checklist verifies live `.hermes` cron points to upgraded scripts.
- Agent B risks are resolved or recorded.

Risks:

- Extracted `.hermes` files may include redacted environment references but still need careful review before commit.
- GitHub publication must avoid tokens, CUI, sensitive vendor data, and private business records.

## 12. Replacement Instructions for Hermes

Use these exact instructions after copying or merging the upgraded repo into live Hermes.

### Authoritative system behavior

Hermes must treat this file as the single authoritative operating plan:

```text
docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md
```

Single-authority rule (Agent B): there must be exactly one replacement-instructions document, and it is this section. `docs/upgrade/HERMES_REPLACEMENT_INSTRUCTIONS.md` must be reduced in Phase 1 to a short stub that says "superseded — see docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md Section 12" and nothing else. If any instruction in `docs/upgrade/` conflicts with this plan, this plan wins and the conflicting file must be stubbed or corrected in the same change that discovers the conflict.

Hermes must also treat these as current authority:

```text
AGENTS.md
approval-hub.md
approval-gates.md
nonbinding-draft-automation-policy.md
operations.md
agents/WFG_SUBAGENT_PROFILES.md
config/subagents.json
config/drive-review-hub.json
config/approval-routing.json
config/email-draft-policy.md
docs/upgrade/DIGITAL_EMPLOYEE_OPERATING_MODEL.md
docs/upgrade/GOOGLE_DRIVE_REVIEW_HUB.md
docs/upgrade/SUBAGENT_SUITE_AND_PROFILES.md
docs/upgrade/README_DIGITAL_EMPLOYEE_UPGRADE.md
templates/subcontractor_bid_packet/
scripts/wfg_sub_bid_packet.py
scripts/wfg_workflow_pump.py
scripts/wfg_approval_dispatcher.py
scripts/reconcile_wfg_approval_buttons.py
scripts/send_wfg_approval_buttons.py
scripts/wfg_email_draft_sync.py
scripts/wfg_gmail_drafts.py
```

### Ignore or replace old behavior

Hermes must stop using these old behaviors:

- Treating subcontractor packets as loose markdown summaries.
- Exposing internal uncertainty, missing facts, WFG strategy, or AI confusion to subcontractors.
- Running only `scripts/reconcile_wfg_approval_buttons.py` as a dead-end approval job.
- Creating Gmail drafts or sends for placeholder recipients.
- Sending outreach because a draft exists.
- Treating "looks good", silence, or an emoji as approval.
- Treating a skill file as if it is a full subagent worker.
- Treating final package approval as automatic submission.
- Treating old `/home/nick/workspace/gov-contracting` path defaults as current except through compatibility wrappers.
- Running `scripts/wfg_sub_bid_packet.py` before `scripts/wfg_research_preflight.py` reports PASS. The packet builder is a renderer, not a research agent.
- Leaving scaffold placeholders in `02_SOLICITATION_BRIEF.md`, `05_SCOPE_DECOMPOSITION.md`, `06_SUBCONTRACTOR_SOURCING_CRITERIA.md`, or `attachment_manifest.md`. Unknown facts go in `04_MISSING_INFORMATION.md` with the documents checked.
- Building any competing packet document (Google Doc or otherwise) outside `scripts/wfg_sub_bid_packet.py`.
- Sending outreach by any path other than `scripts/wfg_outreach_cycle.py execute-send` after a recorded GATE_2_SEND approval — including "Nick said go ahead" in chat, which must be recorded as the GATE_2_SEND decision first.

### Required new behavior

For every selected opportunity:

1. Continue all non-binding internal drafting until a true approval gate.
2. Keep Marcus as orchestrator.
3. Use role-bound subagents for specialist work.
4. Use the workflow pump for approval-to-next-task handoff.
5. Use the Google Drive Review Hub for remote review.
6. Use Gmail drafts for email review when configured.
7. Use Telegram approval buttons when available and exact text fallback when not.
8. Never perform external outreach, submission, signing, certification, spending, or external sharing without exact approval.
9. Log every handoff and artifact with path, version, hash, state, and next gate.
10. Create blocker notes instead of improvising when facts are missing or tools fail.

### Canonical packet command

Step 1 is always the research preflight. The packet builder refuses to run without a current PASS:

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
python3 scripts/wfg_research_preflight.py /path/to/opportunity-folder --queue-next
```

If it prints FAIL: fix each numbered item in `research_blocker.md` from the source documents and re-run until PASS. If required facts genuinely do not exist in any document, the blocker is the deliverable — stop there.

Only after PASS:

```bash
WFG_PROJECT_DIR=/home/nick/workspace/wfg-gov-contracting-v2 \
python3 scripts/wfg_sub_bid_packet.py /path/to/opportunity-folder --docx --drive
```

If Drive is not configured:

```bash
python3 scripts/wfg_sub_bid_packet.py /path/to/opportunity-folder --docx
```

Then create a Drive setup blocker.

### Canonical approval pump command

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
python3 scripts/wfg_workflow_pump.py
```

This is the scheduled handoff runner. Do not schedule only the button reconciler.

### Canonical no-send rule

Hermes may draft emails and create Gmail drafts. Hermes may not send, reply, forward, submit, certify, sign, spend, contact third parties, or share externally without a valid approval for the exact action, recipient, version, and hash.

## 13. Risks and Mitigations

Added by Agent B. Each risk names its mitigation and where the mitigation lives.

1. Dispatcher substring matching fires the send path on a wrongly-named gate (verified live hazard at `scripts/wfg_approval_dispatcher.py:157-187`). Mitigation: gate IDs first, refusal on unknown/missing ID, tests. Phase 2, first task.
2. A stale approval authorizes a changed packet, recipient list, price, or package. Mitigation: hash binding, 14-day expiry, amendment auto-void, `superseded` state, GATE_AMEND_CONTINUE to resume. Phase 2.
3. Duplicate outreach. Mitigation: `external_action_ledger` checked with `subcontractor_interactions`, `outreach_sends`, and `gmail_drafts` before any send; ledger backfill and duplicate-block tests merge before any new send worker is enabled. Phases 2 and 5.
4. A human manually sends a pre-approval Gmail draft from the Gmail app. Mitigation: `[PENDING WFG GATE 2]` subject prefix on all pre-approval drafts; send worker composes the real message from approved text and verifies its hash. Phase 5.
5. Approval fatigue turns gates into rubber stamps. Mitigation: two-decision Gate 2 instead of four, one-line recommendation in every packet, component-revision flow, daily brief batching. Section 7.
6. Drive folder renumbering or non-idempotent creation corrupts the review hub. Mitigation: append-only numbering ruling, find-or-create by name. Section 6, Phase 3.
7. Live cron runs against the old `gov-contracting` repo. Mitigation: exact eight-file path fix list in Phase 1, deployment checklist verifying `.hermes` wrappers, Phase 8 check.
8. Skill-name mismatches load the wrong or no SOP. Mitigation: exact 8-entry rename table in Section 2; acceptance check that every `primary_skills` entry resolves to an installed skill directory. Phase 1.
9. Sensitive data exposed via published dashboard or public Drive link. Mitigation: no dashboard publication without authentication (Cloudflare Access or equivalent); MVDE uses Telegram brief + private Drive only; no-public-links rule in Drive Librarian. Phases 3 and 7.
10. Telegram outage or button-registry drift silently strands approvals. Mitigation: pump reconciles on every run, redelivers undelivered requests, stale-approval and stale-blocker escalation in the daily brief. Section 5.
11. Placeholder or contact-form-only recipients reach the draft/send path. Mitigation: guards verified present in workspace scripts (`scripts/wfg_email_draft_sync.py:28,137`); deployment must confirm live copies match; regression test added in Phase 5.
12. Secrets, CUI, or vendor-private data pushed to GitHub. Mitigation: `.hermes` is deployment-only (documented, never committed live), `.env` excluded, no-secret CI check, `DATA_CLASSIFICATION.md` review before first push. Phase 8.
13. Unbounded scope stalls delivery. Mitigation: MVDE definition of done and phase ordering rules in Section 11; six-role roster until volume justifies twelve.

## 14. Debate Notes

### Agent A decisions - Turn 2

Agent A accepts Agent B's main restructures because they improve operations without weakening safety:

- Gate 2 model is now two decisions: GATE_2_PACKAGE and GATE_2_SEND. Four sequential mobile approvals are rejected as too annoying for real use.
- MVDE uses six active roles. The full agent suite remains the target-state architecture, but inactive roles are checklists/skills until volume justifies active workers.
- State migration is deferred to Phase 2 and done once, with gate IDs and transition-table changes in the same change.
- DB-first queue is accepted. Kanban is a visibility mirror, not the source of truth.
- Drive mirrors review-ready artifacts only. The local repo and SQLite keep the full audit trail.
- Existing Drive folder numbers remain append-only. Do not renumber `07 Proposal Package` or `08 Submission Proof`.
- `external_action_ledger` lands before any new send worker.
- Existing `wfg_phase*` scripts are wrapped during MVDE, not refactored.
- Gmail drafts are created only after packet existence and recipient validation, and pre-approval subjects carry `[PENDING WFG GATE 2]`.
- Published dashboard work waits for authentication. MVDE uses Telegram daily brief plus private Drive.
- Live `.hermes` files are deployment-only; GitHub gets templates/docs, not live state or secrets.
- `docs/upgrade/HERMES_REPLACEMENT_INSTRUCTIONS.md` becomes a pointer stub so Section 12 is the single replacement authority.

### Agent A modifications - Turn 2

- Reconciled the Section 3 intake draft list with `scripts/wfg_phase2.py`, which currently writes `00` through `12`.
- Added concrete Gate 1 packet contents based on current artifacts.
- Added `00 Command Snapshot/` contents for Drive.
- Added migration scope from the extracted SQLite DB:
  - `discovered`: 23,602 rows
  - `awaiting_pursue_decision`: 3 rows, including one test fixture
  - `outreach_approved`: 5 rows
- Added state-migration rules for legacy `outreach_approved` rows with and without sent proof.
- Added ledger backfill for historical sent proof from `gmail_drafts`, `subcontractor_interactions`, and sent-related `workflow_events`.

### Agent B review focus next (answered — historical)

All five questions were answered in Agent B Turn 2 and confirmed in Agent A Turn 3; kept for the record:

- Does Phase 1 have enough file-level detail to start? Yes — exact script lists, config changes, and stub tasks are enumerated in Phase 1.
- Does Phase 2's migration and gate-ID sequencing prevent the dispatcher substring bug? Yes — gate IDs are the first Phase 2 task, with a refusal-on-unknown-ID test, and no new gate names ship before it.
- Does the GATE_2_PACKAGE / GATE_2_SEND flow preserve enough friction while staying mobile-friendly? Yes — two distinct recorded decisions, ledger check between approval and execution, two taps total.
- Are the command snapshot fields sufficient for phone review? Yes — plus the `generated_at` staleness stamp and single-file rule added in Agent B Turn 2.
- Are the backfill rules precise enough for a migration test? Yes after Agent B Turn 2 — exact column, cross-source dedupe, form-contact handling, and idempotence are all testable, and Phase 2 acceptance criteria encode them.

## Agent B Review Notes - Turn 1

Historical review notes from Agent B. Agent A Turn 2 answers the revisit items in the section below; do not treat the unresolved items in this historical note as current disputes unless they still appear in Section 15.

### What I changed

- Section 2: replaced vague "some mismatches" claims with verified specifics — the exact 8 broken skill references, the exact 8 old-path scripts, the dispatcher's substring matching with line numbers, the Approve/Deny-only button limitation, the zero test coverage on pump/dispatcher/reconciler, and the verified absence of any send capability.
- Section 5: added the critical dispatcher sequencing constraint (new gate names would trigger the send path under current code); ruled DB-first task queue with Kanban as tolerant mirror; deferred the physical state migration to Phase 2; added heartbeat timeout, Telegram outage, Gmail credential, and dead-letter failure handling.
- Section 6: ruled Drive scope to review-ready artifacts only; fixed the folder renumbering collision with the live `config/drive-review-hub.json` tree (append-only numbering); corrected the What-goes-where mappings.
- Section 7: restructured Gate 2 from four approvals to GATE_2_PACKAGE + GATE_2_SEND with a component-revision flow; added machine gate IDs to every gate; added expiry defaults and a fixed decision vocabulary; added the missing GATE_AMEND_CONTINUE gate; added GATE_5_SUBMIT hash confirmation; added the approver-identity/Managing-Member evidence design and the mobile approval flow.
- Section 9: aligned the outreach approval text to the two-decision model.
- Section 11: added the MVDE scope with a six-role roster and a definition of done; added phase ordering rules; added exact file lists, the replacement-instruction stub task, `config/approvers.json`, dispatcher/pump tests, the ledger-before-connector rule, and the Gmail draft subject-prefix safeguard.
- Section 12: added the single-authority rule and replaced the blanket `docs/upgrade/*` authority with an explicit file list.
- Added Section 13 Risks and Mitigations (13 risks, each with mitigation and phase).

### What I agree with

Agent A's core architecture is right and I did not touch it: Drive as review hub / Telegram as decision surface / local repo as vault; the workflow pump as the single scheduled handoff runner; version-and-hash-bound approvals; the external action ledger; skills-versus-subagents separation; the packet system as the post-Gate-1 path; internal uncertainty never reaching subcontractor-facing material; Gates 4 and 5 staying separate; human-only submission.

### What I disagree with

- Gate 2 as four sequential approvals (rewritten to two; A may contest).
- Twelve active subagent roles at current volume (six for MVDE; the rest are target-state).
- Adopting the 24-state opportunity model immediately (deferred to Phase 2 with migration).
- The Drive tree renumbering (collided with the live config; corrected).
- "Docs/upgrade/* is current authority" while also deprecating old instructions (contradictory; now an explicit list plus a stub rule).

### What Agent A must revisit in Turn 2

1. Accept or contest the GATE_2_PACKAGE / GATE_2_SEND model — if contesting, address approval fatigue on mobile concretely.
2. Accept or contest the six-role MVDE roster and the deferral of Phases 4/6/7/8.
3. Specify Gate 1's approval packet contents against the current intake reality — Section 3 lists eight intake drafts (00 through 12) but nothing verifies which of those `wfg_phase2.py` actually produces today; reconcile the list with the code or mark which drafts are new work.
4. Define the command snapshot doc (`00 Command Snapshot/`) content — it is in the tree but never specified.
5. Confirm the state-migration script scope: which existing rows in `state/wfg_workflow.sqlite3` need mapping, and whether any live opportunity is mid-pipeline during migration.

### Top implementation risks

The dispatcher substring bug interacting with new gate names (risk 1), duplicate outreach before the ledger exists (risk 3), and live cron running old paths (risk 7). All three are addressed by the phase ordering rules; they are the reason those rules exist.

### Is the plan ready to build?

Phase 1 is ready to build now. Phase 2 is ready once Agent A confirms the Gate 2 model and the state-migration scope. Phases 3 and 5 (MVDE subsets) are ready in design but blocked on Phase 2 by the ordering rules. Everything past MVDE is direction, not spec, and should stay that way until MVDE has run a real opportunity.

## Agent A Review Notes - Turn 2

### What I changed

- Accepted the two-decision Gate 2 model and removed the four-approval dispute.
- Accepted the six-role MVDE roster while preserving the full target-state agent suite as future architecture.
- Reconciled Section 3 with `scripts/wfg_phase2.py`; the current intake creates drafts `00` through `12`, not only the eight Agent A originally listed.
- Added exact Gate 1 packet contents.
- Defined `00 Command Snapshot/` contents for Drive and mobile review.
- Scoped the Phase 2 state migration using the extracted SQLite DB counts and current mid-pipeline rows.
- Added historical sent-proof ledger backfill before any new send worker.
- Clarified that `wfg_phase*` scripts are wrapped during MVDE and refactored only after the loop proves itself.
- Tightened Phase 3 to an MVDE Drive subset first, with full review-ready tree automation later.

### What I accepted from Agent B

- GATE_2_PACKAGE + GATE_2_SEND.
- DB-first workflow queue with Kanban as a tolerant mirror.
- Ledger-before-send implementation order.
- Review-ready Drive scope.
- Append-only Drive folder numbering.
- GATE_5_SUBMIT hash confirmation.
- `config/approvers.json` as approver evidence.
- Dashboard publication only after authentication.
- `.hermes` as deployment-only, not committed live state.
- Skill config renaming to installed skill names, no alias skill folders.
- Section 12 as the single replacement-instruction authority.

### What I rejected or modified from Agent B

- I did not reject any major Agent B ruling.
- I modified the "no send code exists" framing: current repo scripts do not expose a send worker to build on, but the extracted DB contains historical sent-proof records. The plan now requires backfilling those records into `external_action_ledger` before new send execution is enabled.
- I kept the full subagent suite in Section 4 as the target-state architecture, while accepting six active roles for MVDE.

### Remaining disputes

- None.

### Confidence score

0.88

### Whether consensus is reached

YES. The plan is specific enough to implement Phase 1 immediately and Phase 2 after the gate-ID/migration work begins. The approval model is safe, workflow handoffs are explicit, Drive review is mobile-readable, the MVDE subagent model is clear, subcontractor packet integration is clear, and old-instruction conflicts are addressed through the single-authority rule.

## Agent B Review Notes - Turn 2

### What I changed

- Section 5 migration spec: tightened to test-precision. Named the exact source column (`opportunities.workflow_status` — the table has no `status` column, which would have broken the migration script's first query); made the test-fixture filter explicit (`is_test_fixture=0 AND environment='production'` for all reporting); added the cross-source backfill dedupe rule (the same physical send appears in up to three tables — 8 sent Gmail drafts, 12 outbound email interactions, and 8 sent-related workflow events overlap; without deduping by opportunity + recipient + date the ledger would triple-count history); added the non-email contact rule (6 `contact_form`/`form` outbound interactions exist with no recipient email — keyed by interaction and flagged `needs_human_review`); defined duplicate semantics for `historical_sent_proof` rows (block same opportunity + recipient; disclose prior contact in any new GATE_2_PACKAGE; never block other opportunities); added the idempotent `--apply` requirement.
- Phase 2 acceptance criteria: added migration and backfill tests — dry-run must reproduce the verified counts, `--apply` twice is a no-op, deduped backfill rows carry `sources_json`, historical proof blocks a duplicate send in a test, and fixtures never leak into production counts.
- Section 6 command snapshot: added a `generated_at`/state stamp so staleness is self-evident on a phone, and the single-file overwrite rule so Drive holds one snapshot per opportunity instead of accumulating dated copies.

### What I accepted from Agent A

- The correction of my Turn 1 framing: I said no sends had ever happened; Agent A found historical sent-proof rows in the DB. Verified this turn — one outreach round of 4 emails plus a Q&A email and 8 sent Gmail drafts are real, plus 6 form-based contacts I found that Agent A's list missed the significance of. The ledger backfill requirement Agent A added is correct and is now mandatory before the send worker exists.
- The intake reconciliation: verified `scripts/wfg_phase2.py` writes drafts `00` through `12` (checked the draft-generation table in the code, lines 282-294).
- The migration counts: verified exact against the extracted DB (23,602 / 3 / 5, with 2 test fixtures splitting as Agent A stated).
- The `00 Command Snapshot/` field list, the Gate 1 packet contents, the `outreach_approved`-without-proof rule ("never execute a legacy broad Gate 2 approval automatically" is exactly right), and the MVDE Drive subset in Phase 3.

### What I rejected or modified from Agent A

- Nothing rejected. All modifications were precision-tightening of accepted designs, listed above. Agent A's Turn 2 claims all survived verification, which was not true of my own Turn 1 "no sends ever" claim — noted for symmetry.

### Remaining disputes

- None.

### Confidence score

0.9

### Whether consensus is reached

YES. Against the consensus rule: the plan is specific enough to implement (Phase 1 has file-level tasks; Phase 2 has a named migration script with verified counts and testable acceptance criteria); both agents' major concerns are resolved (Gate 2 model, MVDE scope, dispatcher sequencing, ledger-first, backfill); the approval model is safe (gate IDs, hash binding, expiry, four-decision vocabulary, GATE_5 hash confirmation, approver registry); handoffs are explicit and recoverable (DB-first queue, idempotency keys, pump resume rules); Drive review is standardized and mobile-readable (append-only numbering, review-ready scope, single-file command snapshot); the subagent model is real (six active MVDE roles with missions and boundaries, twelve-role target state); packet integration is concrete (authoritative files, source map, internal/external separation); old-instruction conflicts are addressed (single-authority rule, stub tasks, deprecated-behavior list); and the roadmap is phased with acceptance criteria per phase. Residual 0.1: implementation will surface surprises the plan cannot pre-answer — live `.hermes` versus workspace drift and Gmail API behavior under real credentials being the likeliest.

## Agent A Review Notes - Turn 3

### What I changed

- Performed a final consistency pass after Agent B Turn 2.
- Verified the key repo facts Agent B tightened: `opportunities.workflow_status` is the real state column; production/test fixture counts match; `gmail_drafts`, `subcontractor_interactions`, and sent-related `workflow_events` contain the historical proof counts used in the migration spec.
- Consolidated duplicated Phase 2 migration acceptance criteria into four testable checks: dry-run counts, idempotent apply with backup/events, deduped ledger backfill with `sources_json` and `needs_human_review`, and historical duplicate-send blocking.
- Updated the document header and consensus counters for Agent A Turn 3.

### What I accepted from Agent B

- All Agent B Turn 2 precision edits: exact migration column, production/test filters, historical backfill dedupe rule, form-contact handling, `historical_sent_proof` semantics, idempotent `--apply`, and command snapshot single-file/staleness rules.
- Agent B's final position that no architectural disputes remain and the next step is execution.

### What I rejected or modified from Agent B

- Nothing rejected.
- Modified only presentation: consolidated overlapping acceptance criteria so Phase 2 is easier to implement and test.

### Remaining disputes

- None.

### Confidence score

0.91

### Whether consensus is reached

YES. This is now an implementation-ready consensus plan. Remaining uncertainty is implementation risk, not architecture: live `.hermes` drift, credentials, and Gmail API behavior under real conditions.

## Agent B Review Notes - Turn 3

### What I changed

- Verification pass, not new design. Confirmed Agent A's Turn 3 consolidation of the Phase 2 acceptance criteria is lossless: all four migration/backfill checks from my Turn 2 survive (dry-run counts, idempotent apply with backup and events, deduped backfill with `sources_json` and `needs_human_review`, historical duplicate-send block including the different-opportunity clause). Confirmed the derived production count of 23,601 `discovered` (23,602 minus one test fixture) is arithmetically correct against the DB.
- Re-checked the whole document for stale references: no Gate 2A-D remnants, no orphaned state names, section numbering 1-15 intact, Section 5 idempotence requirement intact.
- Marked Agent A's "review focus next" questions in Section 14 as answered with pointers, so the final document carries no open questions.
- Updated the header status and consensus counters.

### What I accepted from Agent A

- The Turn 3 consolidation in full. Merging my overlapping acceptance bullets into four testable checks made Phase 2 easier to implement without weakening anything — the right kind of edit for a closing turn.

### What I rejected or modified from Agent B (self) or Agent A

- Nothing. No new disputes were raised and none were warranted; adding further changes at this point would be churn masquerading as rigor.

### Remaining disputes

- None.

### Confidence score

0.91

### Whether consensus is reached

YES. Both agents have independently verified the load-bearing facts against the repository and database (dispatcher matching, skill mappings, path defaults, DB counts, historical send proof), the safety-critical sequencing rules are encoded as phase-ordering constraints with tests, and the plan defines a first usable version (MVDE) with a concrete definition of done. The debate is closed; the correct next activity is building Phase 1, and the first pull request should be judged against Section 11's acceptance criteria, not against further debate.

## Agent A Review Notes - Turn 4

### What I changed

- Performed a closing verification pass after Agent B Turn 3.
- Confirmed the plan remains internally consistent across MVDE scope, two-decision Gate 2, DB-first workflow handoffs, append-only Drive review structure, subcontractor packet separation, duplicate-send prevention, failure recovery, GitHub readiness, and old-instruction replacement.
- Updated the document header and Section 15 consensus counters.
- Added no new architecture requirements; the plan is ready for execution and should now be judged by Phase 1 and Phase 2 acceptance criteria.

### What I accepted from Agent B

- Accepted Agent B Turn 3 as a verification-only closeout, not a reason to reopen design.
- Accepted the Section 14 cleanup marking the previous review-focus questions as answered.
- Accepted that there are no remaining architectural disputes and that the first pull request should be reviewed against Section 11, not against additional debate criteria.

### What I rejected or modified from Agent B

- Nothing rejected.
- Modified only consensus metadata and added this final Agent A closeout note.

### Remaining disputes

- None.

### Confidence score

0.92

### Whether consensus is reached

YES. The approval model is safe, handoffs are explicit and recoverable, Drive review is clear for mobile operation, the MVDE subagent model is bounded, subcontractor packet integration is professional, audit/failure recovery is adequate for the first build, old system conflicts are addressed, and the roadmap is phased and testable. Remaining uncertainty is implementation risk, not architecture.

## 15. Consensus Status

Consensus reached: YES

Agent A turn count: 4 of 5

Agent B turn count: 3 of 5

Remaining disputes:

- None. Debate closed by mutual confirmation after Agent A Turn 4 and Agent B Turn 3.

Build order from here: Phase 1 (stabilize) -> Phase 2 (gate IDs, tables, migration, tests) -> MVDE subsets of Phases 3 and 5 -> first real opportunity end-to-end -> then and only then the deferred phases.
