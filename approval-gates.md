# WFG Approval Gates

Rule: no AI, subagent, script, cron job, or skill output equals company approval. Binding business decisions require explicit authorized-human approval, documented in an approval log.

Internal drafting rule: creating, revising, analyzing, or storing an internal draft is not itself an approval gate. Marcus and WFG subagents should automatically continue non-binding drafting for selected opportunities under `/home/nick/workspace/wfg-gov-contracting-v2/nonbinding-draft-automation-policy.md` until a true authorization gate is reached.

Central approval routing rule: all WFG agents and subagents must route true authorization gates through the Approval Coordinator / WFG Approvals hub so approvals stay in one place across agents, opportunities, and Telegram topics. Use `/home/nick/workspace/wfg-gov-contracting-v2/approval-hub.md`, the pending inbox `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/`, and the Telegram topic `telegram:-1003889564123:295` (`WFG Approvals`). Agents should point to files, emails, documents, pricing worksheets, proposal drafts, forms, and opportunity folders that Nick must review instead of scattering approval requests across working channels.

## Required approvals

- BID decision after bid/no-bid review when moving into live pursuit.
- Outreach send authority unless a standing outreach rule exists.
- Buyer clarification questions.
- Subcontractor reliance for compliance, quote, licensing, insurance, bonding, or similarly situated status.
- Pricing assumptions, markup, contingency, discounts, and final bid price.
- Proposal finalization and submission.
- Contract acceptance after award.
- Scope changes, claims, waivers, admissions of fault, or payment releases.
- Any email, message, quote request, call, or other external communication before it is sent.
- Any final bid price, representation, certification, signature, submission, amendment acknowledgment, financial commitment, external sensitive-information upload, award acceptance, purchase order, contract, modification, or notice to proceed.

## Consolidated approval request format

At each required approval gate, Marcus should present one consolidated approval request containing:

- What has already been drafted.
- What remains incomplete.
- Important risks.
- Assumptions requiring confirmation.
- The exact action that requires authorization.
- The exact draft communication, price, or submission package being approved.

Silence is never approval. After approval for a stage, Marcus should automatically continue all remaining non-binding drafting work until the next true authorization gate.

## Approval log fields

Opportunity/Contract ID:
Approval Type:
Approver:
Timestamp:
Decision:
Exact Approval Text:
Related Artifact:
Next State:
Exception Notes:

## Managing Member control evidence

For any decision tied to WOSB/veteran-owned positioning or company control, preserve evidence that the 51% owner had real oversight and approval authority. Nick may prepare, recommend, draft, analyze, and administer, but the 51% owner must retain genuine control for any claimed eligibility path.
