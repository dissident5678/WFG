# Google Drive Review Hub

## Purpose

The Drive Review Hub gives Nick a clean remote dashboard for active bids, draft packets, approvals, outreach drafts, quotes, proposal packages, and submission proof.

## Folder tree

```text
WFG Review Hub/
  SAM Opportunities/
    2026/
      <notice-id-or-opportunity-slug>/
        01 Source Docs/
        02 Internal Review/
        03 Subcontractor Packet/
        04 Approvals/
        05 Draft Emails/
        06 Quotes Received/
        07 Proposal Package/
        08 Submission Proof/
```

## What belongs where

### 01 Source Docs

Original solicitation, amendments, attachments, drawings, specs, wage determinations, Q&A, and SAM.gov export files.

### 02 Internal Review

WFG-only analysis: risk register, missing information, source map, bid/no-bid notes, internal review summary, quote comparison, pricing review.

### 03 Subcontractor Packet

Subcontractor-facing packet files:

```text
subcontractor_bid_packet.docx
subcontractor_bid_packet.md
```

### 04 Approvals

Gate approval packets and button decision logs.

### 05 Draft Emails

Draft outreach messages and Gmail draft references. Drafts are not sent unless Gate 2 approval authorizes the exact recipients/message/version.

### 06 Quotes Received

Subcontractor quote PDFs, emails, scope clarifications, exclusions, and quote normalization sheets.

### 07 Proposal Package

Final bid forms, pricing sheets, technical package, certifications, and submission package drafts.

### 08 Submission Proof

Screenshots, sent email proof, portal confirmation, timestamp, and final archive.

## Required environment

```bash
GOOGLE_TOKEN_PATH=/home/nick/.hermes/google_token.json
WFG_DRIVE_ROOT_FOLDER_ID=<folder-id>
```

`WFG_DRIVE_ROOT_FOLDER_ID` is optional but recommended. If omitted, the bid packet script attempts to find or create `WFG Review Hub` privately.

## Sharing rule

No public links. No external sharing from the Drive script. External sharing should be a separate approved send/share workflow that names:

```text
file
recipient
version/hash
purpose
approval ID
```
