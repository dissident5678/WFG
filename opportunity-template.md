# Opportunity Folder Template

Create one folder per opportunity:
/home/nick/workspace/wfg-gov-contracting-v2/opportunities/YYYY-MM-DD-agency-short-title/

Recommended structure:
- source-link.txt
- opportunity_manifest.md
- 00_INTAKE.md
- solicitation-docs/
- raw-sam-record.json
- attachment_manifest.md
- 01_BID_NO_BID_SCORECARD.md
- 02_SOLICITATION_BRIEF.md
- 03_COMPLIANCE_MATRIX.md
- scope_sheets/
- subcontractors.csv
- outreach-log.md
- quotes/
- pricing/
- proposal/
- 09_AUDIT_REPORT.md
- 11_SUBMISSION_CHECKLIST.md
- approvals/
- submission-proof/
- amendment_log.md
- notes.md

Opportunity manifest fields:
- Opportunity ID:
- Solicitation number:
- Title:
- Agency / office:
- Buyer contact:
- Set-aside / eligibility:
- NAICS:
- PSC:
- Place of performance:
- Due date and timezone:
- Q&A deadline:
- Submission method:
- Current status:
- Current owner subagent/skill:
- Human gate needed:
- Source link:
- Documents:
- Scope summary:
- Key risks:
- Next action:

Subagent rule: each output should identify the subagent role, loaded skill, verified facts, assumptions, estimates, missing information, source files, risks, and next true human authorization gate.

Default automation rule: for any opportunity Nick directs Marcus to intake, automatically prepare every available non-binding internal draft listed in `/home/nick/workspace/wfg-gov-contracting-v2/nonbinding-draft-automation-policy.md` until a true authorization gate is reached. Do not stop merely because a draft contains placeholders such as `[USER INPUT REQUIRED]`, `[PRICE NOT APPROVED]`, `[SUBCONTRACTOR NOT VERIFIED]`, `[DOCUMENT MISSING]`, or `[ASSUMPTION — MUST BE CONFIRMED]`.
