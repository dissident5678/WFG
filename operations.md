# WFG Operations Architecture

This file translates the imported Wright Foster Group Hermes architecture into the preferred operating model: Marcus remains available to Nick, and specialized subagents use WFG skills to do the work.

## Top-level flow

Default opportunity behavior: when Nick directs Marcus to intake an opportunity, Marcus should automatically run or delegate every non-binding internal drafting step that can be completed from available information. Marcus should not stop after each internal artifact merely to ask permission to draft the next one. The standing policy is `/home/nick/workspace/wfg-gov-contracting-v2/nonbinding-draft-automation-policy.md`.

Non-binding drafting includes intake, bid/no-bid scorecard, solicitation brief, compliance matrix, missing-information lists, subcontractor sourcing criteria/candidate lists, draft quote requests, draft emails/messages/call scripts, validation checklists, quote-comparison and exclusion analysis, preliminary pricing worksheet, suggested overhead/contingency/profit ranges, LOS analysis when applicable, technical proposal drafts, verified-only past-performance draft sections, required-form checklists, final compliance audit, red-team review, submission checklist, draft submission email, and draft follow-up/outcome-tracking messages.

Use placeholders instead of stopping whenever possible: `[USER INPUT REQUIRED]`, `[PRICE NOT APPROVED]`, `[SUBCONTRACTOR NOT VERIFIED]`, `[DOCUMENT MISSING]`, and `[ASSUMPTION — MUST BE CONFIRMED]`. Clearly separate verified facts, assumptions, estimates, missing information, and risks in each artifact.

1. Intake
   Marcus receives a SAM link, solicitation, pasted opportunity, or search digest item. Marcus creates or assigns an Opportunity Intake Subagent using wfg-opportunity-intake, then continues automatically into all available non-binding drafting steps until a true authorization gate is reached.

2. Bid/No-Bid
   Marcus delegates scoring to the Bid/No-Bid Strategy Subagent using wfg-bid-no-bid. Marcus returns the recommendation to Nick and requests the needed human decision.

3. Solicitation Reading
   Marcus delegates document extraction to the Solicitation Reader Subagent using wfg-solicitation-reader. Output must include solicitation brief and compliance matrix.

4. Sourcing and Outreach Prep
   Marcus delegates candidate finding to the Subcontractor Scout Subagent using wfg-subcontractor-scout and outreach drafting to the Outreach Drafter Subagent when useful. Marcus asks Nick or the authorized owner before sending anything unless a standing send rule has been approved.

5. Validation
   Marcus delegates credential checks to the Validator Subagent using wfg-subcontractor-validator and wfg-validator-pro.

6. Pricing
   Marcus delegates quote normalization and LOS analysis to the Estimator / LOS Subagent using wfg-estimator-los, wfg-pricing-workbook-agent, and wfg-los-calculator.

7. Proposal
   Marcus delegates draft assembly to the Proposal Compiler Subagent using wfg-proposal-compiler and proposal-library support as available.

8. Audit
   Marcus delegates final red-team review to the Compliance Auditor Subagent using wfg-compliance-auditor and wfg-submission-checklist-agent.

9. Human Submission
   Marcus prepares the final status, risks, checklist, and any draft submission email/package for Nick/authorized owner. Marcus does not submit automatically.

10. Archive / Historian
   Marcus delegates win/loss, debrief, and past-performance tracking to the Historian Subagent using wfg-historian-tracker.

## Availability rule

When Nick is actively talking to Marcus, Marcus should avoid disappearing into long specialist work. Marcus should split work into subagent tasks, report that the workers are running, and keep the conversation clear. If a task must run for a long time or recur, Marcus should convert it into a Kanban card or cron job instead of blocking the main conversation.

## Standard subagent prompt pattern

You are a Wright Foster Group specialist subagent. Work only within /home/nick/workspace/wfg-gov-contracting-v2 and the specified opportunity/contract folder. Load and follow these skills: [skill names]. Follow `/home/nick/workspace/wfg-gov-contracting-v2/nonbinding-draft-automation-policy.md`: produce every non-binding internal draft available from the supplied information, use placeholders instead of stopping when possible, and clearly separate verified facts, assumptions, estimates, missing information, and risks. Produce these files: [file list]. Do not make binding commitments, send external messages, contact third parties, claim certifications, approve final pricing, sign documents, submit bids/responses/amendments, accept awards, spend money, or upload sensitive information externally. At the first true authorization gate, follow `/home/nick/workspace/wfg-gov-contracting-v2/approval-hub.md`: create/return one consolidated approval packet, write or request registration under `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/`, include paths to files/emails/documents/pricing/proposal materials Nick must review, and route it to Marcus or the Approval Coordinator for delivery to `telegram:-1003889564123:295` (`WFG Approvals`). Return a concise summary to Marcus.

## Installed WFG skills

The imported WFG skills were installed under /home/nick/.hermes/skills/business-ops/ so future Hermes subagents can be launched with task-specific skills.

Installed skills:
- wfg-amendment-monitor
- wfg-bid-no-bid
- wfg-clause-librarian
- wfg-compliance-auditor
- wfg-cui-security-gatekeeper
- wfg-estimator-los
- wfg-historian-tracker
- wfg-los-calculator
- wfg-opportunity-intake
- wfg-outreach-drafter
- wfg-performer
- wfg-pricing-workbook-agent
- wfg-project-controls-agent
- wfg-proposal-compiler
- wfg-proposal-library-manager
- wfg-sam-api-watcher
- wfg-solicitation-reader
- wfg-sub-crm-manager
- wfg-subcontractor-scout
- wfg-subcontractor-validator
- wfg-submission-checklist-agent
- wfg-validator-pro
## Subcontractor Bid Packet Operating Rule

After Gate 1 pursuit approval, build a subcontractor packet before outreach:

```bash
python3 scripts/wfg_sub_bid_packet.py /path/to/opportunity-folder --docx --drive
```

The subcontractor-facing DOCX must be clean and pricing-focused. Missing facts, conflicts, and internal strategy stay in `subcontractor_bid_packet/internal_review_summary.md`. Only send the packet after Gate 2 approval for the exact recipient list, message, packet version, and packet hash.
