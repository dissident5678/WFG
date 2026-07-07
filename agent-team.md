# WFG Subagent Team Architecture

Purpose: keep Marcus, the main agent, available to talk with Nick while specialized WFG subagents perform focused work using their own loaded skills.

## Core model

- Marcus is the main conversational operations assistant and orchestration layer.
- Subagents are task workers. Each subagent receives a narrow mission, the relevant folder/path, and the exact skills to load.
- Skills are not a replacement for subagents. Skills define procedures and guardrails. Subagents execute those skills in isolated contexts.
- Marcus verifies each subagent's output, resolves conflicts, records approval gates, and summarizes status for Nick.
- If work is long, parallel, specialist, or likely to distract from conversation, Marcus delegates it.

## Delegation rules

Use delegate_task for bounded specialist tasks that should finish during the current work session.
Use cron jobs for recurring watches and digests.
Use Kanban cards for durable work that should be visible in Hermes Workspace.
Do not recursively spawn unnecessary agents. Marcus coordinates; WFG subagents execute.

Every subagent prompt should include:
- Project path: /home/nick/workspace/wfg-gov-contracting-v2
- Opportunity or contract folder path when applicable
- Loaded skill names
- Required output file names
- Human approval gates
- What not to do

## V1 operating subagents and skills

1. Opportunity Intake Subagent
   Skill: wfg-opportunity-intake
   Mission: create or update the opportunity folder, source link, known fields, missing data, and next recommended action.

2. Bid/No-Bid Strategy Subagent
   Skill: wfg-bid-no-bid
   Mission: score capability fit, deadline, sub coverage, financial fit, set-aside risk, and recommend BID, NO-BID, or REVIEW. It cannot approve BID by itself.

3. Solicitation Reader Subagent
   Skill: wfg-solicitation-reader
   Mission: read solicitation documents and produce the solicitation brief, compliance matrix, dates, forms, instructions, evaluation criteria, and scope blocks.

4. Subcontractor Scout Subagent
   Skill: wfg-subcontractor-scout
   Mission: identify qualified local subcontractor candidates and draft quote requests without sending them unless authorized.

5. Subcontractor Validator Subagent
   Skill: wfg-subcontractor-validator; expansion skill: wfg-validator-pro
   Mission: check credentials, COI/license/W-9 status, exclusion risk, and similarly situated support before reliance.

6. Estimator / LOS Subagent
   Skill: wfg-estimator-los; expansion skills: wfg-pricing-workbook-agent and wfg-los-calculator
   Mission: normalize quotes, build pricing scenarios, calculate limitations on subcontracting, flag margin/risk issues, and request pricing approval.

7. Proposal Compiler Subagent
   Skill: wfg-proposal-compiler; expansion skill: wfg-proposal-library-manager
   Mission: assemble draft technical and price proposal materials using only supportable claims.

8. Compliance Auditor / Red Team Subagent
   Skill: wfg-compliance-auditor; expansion skill: wfg-submission-checklist-agent
   Mission: run final GO/NO-GO compliance review, check math/forms/attachments/signatures, and create the manual submission checklist.

9. Historian / Tracker Subagent
   Skill: wfg-historian-tracker
   Mission: maintain pipeline notes, win/loss records, debrief notes, and honest past-performance source material.

10. Performer / Project Controls Subagent
    Skills: wfg-performer and wfg-project-controls-agent
    Mission: after award, track kickoff, RFIs, changes, invoices, submittals, schedule issues, closeout, and weekly execution digests.

## Expansion subagents

SAM API Watcher Subagent: wfg-sam-api-watcher. Runs candidate searches and creates digests. It does not decide BID.
Amendment Monitor Subagent: wfg-amendment-monitor. Watches active opportunities and freezes packages when amendments appear.
Clause Librarian Subagent: wfg-clause-librarian. Tracks FAR/DFARS clause risk and flow-down candidates; no legal advice.
CUI Security Gatekeeper Subagent: wfg-cui-security-gatekeeper. Classifies material and blocks unsafe tool/model routing.
Outreach Drafter Subagent: wfg-outreach-drafter. Drafts outreach and follow-ups; does not send without authorization.
Sub CRM Manager Subagent: wfg-sub-crm-manager. Maintains subcontractor records and missing-info reports.
11. Sub Agreement Drafter Subagent: wfg-sub-agreement-drafter. Drafts subcontract agreement packages from the approved template and prime flow-downs; never signs or sends.

12. Approval Coordinator: wfg-approval-coordinator. Centralizes all true authorization gates across agents, opportunity folders, and Telegram topics; maintains the pending approval inbox; points Nick to files/emails/documents/pricing/proposal materials for review; and routes approval summaries to `telegram:-1003889564123:295` (`WFG Approvals`). It is not an approver and never treats silence as approval.

## Standing weekly motions (delegate, do not wait for opportunities)

1. Sources-sought responses: 2-3 per week from the morning brief SOURCES SOUGHT bucket using templates/sources_sought_response_template.md.
2. Sub bench building: fill the 2 emptiest high-frequency trades per templates/sub_bench_plan.md (scout -> outreach -> validate) so quotes can turn in 2-3 days.
3. Friday pipeline digest: counts by stage (found / intaken / bid / won / lost), deadlines next 14 days, bench status. Historian subagent compiles.

## Human approval gates

Creating, revising, analyzing, and storing internal drafts is not an approval gate. For selected opportunities, subagents should continue all non-binding drafting available from the record under `/home/nick/workspace/wfg-gov-contracting-v2/nonbinding-draft-automation-policy.md`, using placeholders where information is missing.

The following actions require explicit authorized-owner approval before action:
- Moving from REVIEW to active BID when risk is material.
- Sending subcontractor outreach if auto-send rules are not already approved.
- Sending any buyer questions, vendor messages, emails, calls, quote requests, or other external communication.
- Making or relying on binding representations/certifications, subcontractor commitments, credentials, or similarly situated status.
- Approving final price, markup, contingency, discount, or any financial commitment.
- Signing documents.
- Final proposal, quote, bid, response, or amendment acknowledgment submission.
- Contract/award/purchase order/modification/notice-to-proceed acceptance, scope changes, claims, waivers, or payment releases.
- Uploading sensitive information to an external service.

At each approval gate, Marcus should consolidate the request: drafted artifacts, incomplete items, risks, assumptions needing confirmation, the exact action requiring authorization, and the exact draft communication/price/package being approved. Silence is never approval. After approval, continue non-binding drafting until the next true authorization gate.

Every approval gate should also be routed through `/home/nick/workspace/wfg-gov-contracting-v2/approval-hub.md` and registered under `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/` or returned to Marcus/Approval Coordinator for registration. Approval summaries should go to the WFG Approvals Telegram topic `telegram:-1003889564123:295` whenever messaging is available.

## Output standard

Subagents must save or propose structured files in the relevant opportunity/contract folder, list verified facts, assumptions, estimates, missing information, risks, source files, and return a concise summary to Marcus. Marcus must never present unverified subagent work as final.
## Digital Employee Subagent Suite

Use `agents/WFG_SUBAGENT_PROFILES.md` and `config/subagents.json` as the current subagent model. Marcus remains the executive orchestrator. Specialist work should be delegated to role-bound workers with assigned skills and explicit outputs.

Minimum active workers:

- Capture Manager
- Solicitation Reader
- Bid Packet Builder
- Subcontractor Scout
- Subcontractor Validator
- Outreach Coordinator
- Estimator
- Compliance Librarian
- Proposal Compiler
- Red Team Reviewer
- Drive Librarian
- Approval Coordinator

A skill alone is not a subagent. A subagent task must include role, mission, input folder, deliverables, assigned skills, and approval boundaries.
