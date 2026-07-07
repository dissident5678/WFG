# WFG Hermes Digital Employee Upgrade

This upgrade turns the current WFG Hermes system from a collection of helpful scripts into a controlled operating loop: discover, analyze, draft, package, request approval, execute only the approved next step, then record proof.

## What changed

### 1. Subcontractor bid packet system integrated

The two DOCX files are now part of the repository:

```text
templates/subcontractor_bid_packet/
  Hermes_Subcontractor_Bid_Packet_Instructions.docx
  WFG_Subcontractor_Bid_Packet_Template.docx
```

The old packet builder has been replaced by:

```text
scripts/wfg_sub_bid_packet.py
.hermes/skills/business-ops/wfg-subcontractor-bid-packet/SKILL.md
```

The builder now creates:

```text
subcontractor_bid_packet/
  subcontractor_bid_packet.docx       # sub-facing, clean
  subcontractor_bid_packet.md         # sub-facing backup
  internal_review_summary.md          # WFG-only
  source_map.json                     # WFG-only
  bid_packet_data.json                # WFG-only extraction data
  review_manifest.json                # approval/review manifest
  google_drive_review_bundle.json     # only when Drive upload runs
```

The main rule is fixed: missing facts, conflicts, and internal uncertainty do not appear in the subcontractor packet. They go into the internal review summary.

### 2. Approval-to-next-workflow handoff fixed

The current system had a weak link: Telegram approval buttons were reconciled, but the next workflow was not always dispatched. The cron shell script now calls:

```text
scripts/wfg_workflow_pump.py
```

The pump performs:

```text
Telegram approval buttons -> approval database -> downstream Kanban/internal task -> status update
```

It does not contact anyone externally. It only queues safe internal work.

### 3. Google Drive review hub added

Drive review structure is defined in:

```text
config/drive-review-hub.json
```

Default tree:

```text
WFG Review Hub/
  SAM Opportunities/
    <YEAR>/
      <OPPORTUNITY>/
        01 Source Docs/
        02 Internal Review/
        03 Subcontractor Packet/
        04 Approvals/
        05 Draft Emails/
        06 Quotes Received/
        07 Proposal Package/
        08 Submission Proof/
```

Set this environment variable in Hermes when you create or choose the Drive root folder:

```bash
WFG_DRIVE_ROOT_FOLDER_ID=<google-drive-folder-id>
```

If it is not set, the script tries to create or use a private folder named `WFG Review Hub`.

### 4. Real subagent operating model added

Subagent profiles are now documented in:

```text
config/subagents.json
agents/WFG_SUBAGENT_PROFILES.md
```

Skills are still reusable procedures. Subagents are role-bound execution contexts with their own mission, scope, tools, and approval boundaries. Hermes should not “solve” subagents by creating more skills only.

### 5. Safer Gmail drafts

The Gmail draft sync now blocks invalid placeholders such as:

```text
[CONTACT FORM ONLY - DO NOT SEND YET]
TO VERIFY
unknown
placeholder
```

It creates Gmail drafts only for verified-looking email addresses. It never sends.

## Install into the live Hermes repo

From the live machine, copy this upgraded project into the repository, review the diff, then commit.

Recommended flow:

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
git status
# copy/merge the upgraded files from this package into the repo
git diff --stat
git diff -- scripts/wfg_sub_bid_packet.py scripts/wfg_workflow_pump.py scripts/wfg_approval_dispatcher.py scripts/wfg_email_draft_sync.py scripts/wfg_gmail_drafts.py
git add scripts templates docs agents config .hermes/skills/business-ops/wfg-subcontractor-bid-packet .hermes/scripts/wfg_approval_reconciler.sh
git commit -m "Upgrade WFG Hermes digital employee workflow and subcontractor bid packet system"
```

## Smoke tests

Use a copied/test opportunity folder, not a live bid folder, for the first test.

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
python3 -m py_compile scripts/wfg_sub_bid_packet.py scripts/wfg_workflow_pump.py scripts/wfg_approval_dispatcher.py scripts/wfg_email_draft_sync.py scripts/wfg_gmail_drafts.py
python3 scripts/wfg_sub_bid_packet.py /path/to/test-opportunity --docx
python3 scripts/wfg_workflow_pump.py --no-kanban-dispatch
```

Then check:

```text
/path/to/test-opportunity/subcontractor_bid_packet/review_manifest.json
/path/to/test-opportunity/subcontractor_bid_packet/subcontractor_bid_packet.docx
/path/to/test-opportunity/subcontractor_bid_packet/internal_review_summary.md
```

Do not enable automatic Gmail draft sync until the Gate 2 approval packet format is consistently producing clean outreach drafts.
