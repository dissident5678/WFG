# WFG Non-Binding Draft Automation Policy

Effective default for every Wright Foster Group opportunity Nick directs Marcus to intake.

## Core rule

For every selected opportunity, Marcus and WFG subagents should automatically complete all non-binding internal drafting, analysis, organization, and storage work that can be completed without human authorization.

Do not pause after every internal step. Continue through the opportunity workflow automatically and produce every available internal draft until reaching an action that legally, financially, or externally commits Wright Foster Group LLC.

Do not interpret silence as approval.

## Automatically prepare when applicable

1. `00_INTAKE.md`
2. `01_BID_NO_BID_SCORECARD.md`
3. `02_SOLICITATION_BRIEF.md`
4. `03_COMPLIANCE_MATRIX.md`
5. Missing-document and missing-information list
6. Subcontractor sourcing criteria
7. Subcontractor candidate list
8. Draft subcontractor quote requests
9. Draft emails, messages, and call scripts for outreach
10. Subcontractor validation checklist
11. Quote-comparison and exclusion analysis
12. Preliminary pricing worksheet
13. Suggested overhead, contingency, and profit ranges
14. Limitations-on-subcontracting analysis when applicable
15. Technical proposal draft
16. Past-performance section using only verified information
17. Required-form checklist
18. Final compliance audit
19. Red-team review
20. Submission checklist
21. Draft submission email when email submission is required
22. Draft follow-up and outcome-tracking messages

## Placeholders and labeling

When information is missing, do not stop the entire workflow unless completion is impossible. Continue using clearly marked placeholders:

- `[USER INPUT REQUIRED]`
- `[PRICE NOT APPROVED]`
- `[SUBCONTRACTOR NOT VERIFIED]`
- `[DOCUMENT MISSING]`
- `[ASSUMPTION — MUST BE CONFIRMED]`

Placeholder boundary (hard rule): placeholders are allowed only in initial intake scaffolds and internal analysis drafts. They are forbidden in the research artifacts consumed by the packet builder — `02_SOLICITATION_BRIEF.md`, `05_SCOPE_DECOMPOSITION.md`, `06_SUBCONTRACTOR_SOURCING_CRITERIA.md`, `attachment_manifest.md` — and in anything subcontractor-facing. A fact that cannot be found in the source documents is recorded in `04_MISSING_INFORMATION.md` together with the documents that were checked. "Continue the workflow" means do the research (download, extract, read the sources), not write placeholders past it: `scripts/wfg_research_preflight.py` must report PASS before `scripts/wfg_sub_bid_packet.py` will render a packet, and a research blocker is the correct output when required facts are genuinely unavailable.

Every draft must clearly separate:

- verified facts
- assumptions
- estimates
- missing information
- risks
- source documents or source links

## Stop points requiring explicit human authorization

Marcus and subagents must stop and request explicit human authorization before doing any of the following:

- Sending an email, message, quote request, or other external communication
- Contacting a subcontractor, contracting officer, agency, supplier, or third party
- Agreeing to or approving a final bid price
- Making a legally binding representation or certification
- Signing a document
- Submitting a proposal, quote, bid, response, or amendment acknowledgment
- Accepting an award, purchase order, contract, modification, or notice to proceed
- Spending money or committing Wright Foster Group financially
- Uploading sensitive information to an external service

## Approval request format

At each required approval gate, present one consolidated approval request containing:

- what has already been drafted
- what remains incomplete
- important risks
- assumptions requiring confirmation
- the exact action that requires authorization
- the exact draft communication, price, or submission package being approved

Route each true authorization gate through the central Approval Coordinator hub so approval requests stay in one place across agents and channels:

- Hub policy: `/home/nick/workspace/wfg-gov-contracting-v2/approval-hub.md`
- Pending inbox: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/`
- Template: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/templates/approval-packet-template.md`
- Telegram topic: `telegram:-1003889564123:295` (`WFG Approvals`)

If files, emails, documents, forms, pricing worksheets, proposal drafts, or other review materials are involved, point to their paths in the approval packet and Telegram summary.

After approval for a stage, automatically continue through all remaining non-binding drafting work until the next true authorization gate.

## What is not an approval gate

Separate approval is not required merely to create, revise, analyze, or store an internal draft. Internal drafts may contain placeholders and assumptions as long as they are clearly labeled and are not externally sent, signed, submitted, priced as final, or otherwise used to bind Wright Foster Group.
