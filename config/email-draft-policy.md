# WFG Email Draft Policy

When WFG work produces an email draft for review, the draft must be created in the Marcus Gmail account's Gmail Drafts tab, not only saved as a local markdown file.

## Applies to
- Agency Q&A drafts
- Subcontractor quote requests
- Follow-up emails
- Proposal/submission transmittal drafts
- Approval-related external email drafts
- Any other WFG email intended for human review before sending

## Required behavior
1. Save the local source draft in the opportunity folder for the document vault/audit trail.
2. Create a real Gmail draft using `scripts/wfg_gmail_drafts.py`.
3. Verify the Gmail draft by reading it back from the Gmail API.
4. Record the Gmail draft ID/message ID in `state/wfg_workflow.sqlite3` table `gmail_drafts` and in the relevant opportunity folder when applicable.
5. Do not send the email unless the applicable WFG approval gate has explicitly authorized sending.

## Standard command

```bash
python3 scripts/wfg_gmail_drafts.py from-md /path/to/draft.md \
  --to recipient@example.com \
  --subject "Subject" \
  --dedupe-key notice:<notice_id>
```

For direct body text:

```bash
python3 scripts/wfg_gmail_drafts.py create \
  --to recipient@example.com \
  --subject "Subject" \
  --body "Body text" \
  --dedupe-key notice:<notice_id> \
  --body-source-path /path/to/local/source.md
```

## Safety
Creating a Gmail draft is allowed as non-binding review preparation. Sending, replying, forwarding, or otherwise contacting third parties still requires the applicable WFG approval gate.
