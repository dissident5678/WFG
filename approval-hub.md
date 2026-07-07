# WFG Approval Hub

Purpose: keep every Wright Foster Group authorization request in one place, even when work is happening across multiple agents, opportunity folders, or Telegram topics.

## Telegram approval channel

Primary approval topic: **WFG Approvals**

- Telegram chat: `Wright Foster Group HQ`
- Delivery target: `telegram:-1003889564123:295`
- Topic/thread ID: `295`

All approval requests should be posted or summarized there unless Nick gives a different destination.

## Approval Coordinator role

The Approval Coordinator is not a binding decision-maker. It is a routing and tracking role that collects approval requests, points Nick to the relevant files/drafts, presents the exact action requiring authorization, and records Nick's response when provided.

## What other agents must do

When any WFG agent/subagent reaches a true authorization gate, it must:

1. Stop before the binding/external action.
2. Create an approval packet in the relevant opportunity folder, preferably under `approvals/`.
3. Also register or summarize the request under the central inbox:
   - `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/`
4. Include links/paths to every file, draft email, quote request, pricing worksheet, proposal package, form, or document Nick must review.
5. Return a concise summary to Marcus or the Approval Coordinator.
6. Marcus/Approval Coordinator posts the request to the WFG Approvals Telegram topic.

Subagents normally cannot send Telegram messages directly. They should write the approval packet and return the approval summary to Marcus, who relays it.

## Approval packet required fields

Each approval packet should include:

- Opportunity / project name
- Opportunity folder path
- Approval type
- Current status
- What has already been drafted
- What remains incomplete
- Important risks
- Assumptions requiring confirmation
- Exact action requiring authorization
- Exact draft communication, price, representation, or submission package being approved
- Files/documents/emails/drafts to review, with paths
- Recommended response options
- Approval log location

## Required human authorization actions

Do not proceed without explicit human authorization before:

- Sending an email, message, quote request, or other external communication
- Contacting a subcontractor, contracting officer, agency, supplier, or third party
- Agreeing to or approving a final bid price
- Making a legally binding representation or certification
- Signing a document
- Submitting a proposal, quote, bid, response, or amendment acknowledgment
- Accepting an award, purchase order, contract, modification, or notice to proceed
- Spending money or committing Wright Foster Group financially
- Uploading sensitive information to an external service

Silence is never approval.

## Approval topic message format

Approval requests should now be sent to the WFG Approvals topic as a concise summary with Telegram inline buttons whenever possible.

Button-enabled sender script:

```bash
/home/nick/workspace/wfg-gov-contracting-v2/scripts/send_wfg_approval_buttons.py --packet /path/to/approval-packet.md
```

The script posts to `telegram:-1003889564123:295`, registers the packet in `/home/nick/workspace/wfg-gov-contracting-v2/approvals/button-registry.json`, and adds two buttons:

- ✅ Approve
- ❌ Deny

Button clicks are handled by the Hermes Telegram gateway callback prefix `wfg:`. A click records the decision in `/home/nick/workspace/wfg-gov-contracting-v2/approvals/decision-log.md`, moves the packet from `approvals/pending/` to either `approvals/approved/` or `approvals/closed/`, and edits the Telegram message to show the decision.

After an approval is accepted, Marcus/the downstream workflow must also post a concise status update in the relevant operational topic/channel when that route is configured, so Nick can follow the work where it belongs. Examples:

- Subcontractor outreach approved → post the next-step status in **Subcontractor Sourcing**.
- Final price approved → post the next-step status in **Pricing**.
- Compliance/proposal/submission approvals → post in the matching operational topic.

Important: the button records the authorization decision. It does **not** by itself send emails, contact subcontractors, approve final pricing, submit bids, or perform the underlying action unless a later workflow explicitly reads the recorded approval and proceeds within its authorization scope. Operational follow-up messages must say what was actually triggered or queued, and must not imply external contact/submission happened until verified.

Fallback text-only format, used only when buttons are unavailable:

```markdown
## APPROVAL NEEDED — [Opportunity / Project]

Approval type: [External outreach / final price / submission / award acceptance / etc.]
Opportunity folder: `/path/to/folder`
Review files:
- `/path/to/file1.md`
- `/path/to/draft-email.md`

Exact action requiring authorization:
[Plain-English action]

Reply options:
- APPROVE: [specific action]
- DENY: [specific action]
- REVISE: [requested edits]
- HOLD
```

## Central folders

- Pending approvals: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/pending/`
- Approved records: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/approved/`
- Rejected/held records: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/closed/`
- Templates: `/home/nick/workspace/wfg-gov-contracting-v2/approvals/templates/`
## Approval Workflow Pump Rule

The approval-button reconciler must not be a dead-end. The scheduled approval job must call:

```bash
python3 scripts/wfg_workflow_pump.py
```

The pump reconciles Telegram decisions and dispatches the next safe internal task. External outreach, final pricing, sharing, signing, certification, spending, and submission remain gated by explicit approval of the exact action/version/recipient.
