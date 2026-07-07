# Hermes Replacement Instructions

Paste this into Hermes/Marcus after copying the upgraded files into the IDE/repo.

## Directive

Replace the old WFG subcontractor packet and approval handoff behavior with the upgraded digital employee operating model in this repository.

## Files that are now authoritative

Use these as the current source of truth:

```text
scripts/wfg_sub_bid_packet.py
scripts/wfg_workflow_pump.py
scripts/wfg_approval_dispatcher.py
scripts/wfg_email_draft_sync.py
scripts/wfg_gmail_drafts.py
.hermes/scripts/wfg_approval_reconciler.sh
.hermes/skills/business-ops/wfg-subcontractor-bid-packet/SKILL.md
templates/subcontractor_bid_packet/Hermes_Subcontractor_Bid_Packet_Instructions.docx
templates/subcontractor_bid_packet/WFG_Subcontractor_Bid_Packet_Template.docx
config/drive-review-hub.json
config/subagents.json
agents/WFG_SUBAGENT_PROFILES.md
docs/upgrade/*
```

## Replace old behavior

### Old behavior to stop using

Do not use the old idea that the subcontractor packet is a loose markdown summary or Google Doc that exposes missing information to subcontractors.

Do not run approval reconciliation as a dead-end step that only records button clicks.

Do not create Gmail drafts for placeholder recipients like `[CONTACT FORM ONLY - DO NOT SEND YET]`.

Do not create new “skills” when Nick asks for expert subagents unless the system also creates a worker profile/delegation path.

### New behavior to use

For every pursued SAM.gov opportunity, after Gate 1 approval, queue an internal subcontractor-sourcing task. That task must:

```text
1. Read the opportunity folder and solicitation artifacts.
2. Build the dynamic subcontractor bid packet.
3. Build the internal review summary and source map.
4. Upload a private Drive review bundle when Drive is configured.
5. Draft outreach to specific subs.
6. Create Gate 2 approval with exact recipients, message, packet hash/version, and review links.
7. Do not send anything externally until Gate 2 approval is recorded.
```

Run this command for the packet:

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
python3 scripts/wfg_sub_bid_packet.py /path/to/opportunity-folder --docx --drive
```

If Drive is not configured, run:

```bash
python3 scripts/wfg_sub_bid_packet.py /path/to/opportunity-folder --docx
```

and flag Drive setup as an internal issue.

## Approval handoff rule

The scheduled approval job must call:

```bash
python3 scripts/wfg_workflow_pump.py
```

Do not schedule only:

```bash
python3 scripts/reconcile_wfg_approval_buttons.py
```

The pump is required because it reconciles approval decisions and then dispatches the next internal task.

## Subcontractor packet rule

The subcontractor-facing DOCX must be clean. It may include:

```text
project title
agency
solicitation/notice ID
location
quote deadline
government deadline
site visit
Q&A deadline
brief scope
work items
price sheet/CLINs/units/quantities
attachments needed for pricing
wage/bond/insurance/license/access/safety/schedule requirements that affect pricing
quote form
submission instructions
```

It must not include:

```text
internal confusion
AI uncertainty
WFG bid/no-bid strategy
markup/margin/profit strategy
missing-info warnings
full clause dumps that do not affect pricing
unrelated SAM metadata
```

All uncertainty goes in:

```text
subcontractor_bid_packet/internal_review_summary.md
```

## Subagent rule

Use the profiles in:

```text
agents/WFG_SUBAGENT_PROFILES.md
config/subagents.json
```

When a subagent is needed, create/delegate a task with:

```text
subagent role
mission
input folder
required outputs
assigned skills
approval boundaries
```

Do not confuse skills with agents. A skill is an SOP. A subagent is a worker context using one or more skills.

## Safety boundary

Hermes may draft internally. Hermes may not externally contact, send, submit, certify, sign, spend, or share without the exact approval gate authorizing the exact version/recipient/action.
