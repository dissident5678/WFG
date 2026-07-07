# Agent Debate Brief — WFG Digital Employee System (HISTORICAL)

> **HISTORICAL DOCUMENT.** The debate this brief describes concluded on 2026-07-07
> with consensus. The outcome and current authority is
> `docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md`. Do not act on this
> brief; it is kept for the record only.

Use this brief for the planned agent debate.

## Question

Is the upgraded WFG Hermes system effective, polished, and easy enough to operate as a digital employee for Nick's government contracting business while preserving approval control?

## Evaluation criteria

### 1. Effectiveness

Does the system reliably move opportunities through:

```text
intake -> analysis -> Gate 1 -> sub packet -> outreach draft -> Gate 2 -> outreach -> quotes -> Gate 3 -> proposal -> Gate 4 -> human submission
```

Does each state have a clear owner and next action?

### 2. Polished subcontractor experience

Does the subcontractor packet make WFG look organized?

Check for:

```text
clean DOCX formatting
clear scope
price sheet included when present
deadlines obvious
site visit/Q&A included when relevant
no internal confusion
no AI disclaimers
no missing-info exposure
quote form included
```

### 3. Ease of operation for Nick

Can Nick review remotely from Telegram/Drive without being at the laptop?

Check for:

```text
Drive links
local file paths
version/hash
approval packets
clear recommended action
one-button approval/decline/revise
proof archive
```

### 4. Workflow reliability

Does approval trigger the next internal task automatically through the workflow pump?

Check:

```text
scripts/wfg_workflow_pump.py runs from cron
approval decisions reconcile
approved gates dispatch once
idempotency prevents duplicate tasks
errors are logged
status update is posted
```

### 5. Safety and control

Does the system prevent unapproved external action?

Check:

```text
no automatic send
no automatic submission
no public Drive links
no invalid Gmail recipients
no final price approval without Gate 3
no submission without Gate 4 and human proof
```

## Debate roles

Use at least these reviewers:

```text
Operator: ease of daily use
Capture Manager: opportunity flow quality
Subcontractor: packet clarity
Compliance Reviewer: contract risk
Estimator: quote/pricing usefulness
Automation Engineer: workflow handoff reliability
Red Team: failure modes and embarrassing outputs
```

## Consensus output

The debate should produce:

```text
1. keep/change/remove list
2. top 10 risks
3. top 10 polish improvements
4. missing automations
5. final recommended operating model
6. exact file changes needed
```
