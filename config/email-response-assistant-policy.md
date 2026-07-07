# WFG Email Response Assistant Policy

## Purpose
Monitor Marcus/WFG Gmail for new emails that deserve business review, filter out junk/ads/newsletters/no-response messages, create Gmail draft replies for actionable business-operation messages, and notify Nick.

## Cadence
A single cron runs hourly, while `scripts/wfg_email_response_assistant.py` enforces the actual cadence in Eastern time:

- Never check email between 9:00 PM and 7:00 AM America/New_York.
- Monday-Friday, 7:00 AM-5:00 PM America/New_York: process every 60 minutes.
- Weekends and weekday off-hours from 5:00 PM-9:00 PM: process every 4 hours.

## Classification
Ignore:
- Spam/trash/social/promotions/forums categories.
- No-reply/automated senders.
- Newsletters, advertisements, marketing blasts, and bulk/list mail without a business signal.
- WFG's own sent mail.

Actionable:
- WFG/company/admin emails.
- Govcon/SAM/solicitation/RFQ/RFI/proposal/bid emails.
- Subcontractor/vendor/supplier/quote/scope/pricing/schedule emails.
- Invoices, payments, insurance, banking, registration, tax, EIN, UEI, SAM registration, or similar business administration emails.
- Direct human questions that are not advertising.

## Drafting rule
For every actionable message, create a Gmail draft reply in the same thread when possible. Do not send automatically. The draft must include Nick's WFG signature and an internal note explaining why Marcus classified the email as actionable.

## Notification rule
When one or more drafts are created, the cron script prints a Telegram-ready summary. Empty output means no notification.

## Script
`/home/nick/workspace/wfg-gov-contracting-v2/scripts/wfg_email_response_assistant.py`

## Safety
The assistant may create Gmail drafts and notify Nick. It must not send, forward, delete, archive, mark as spam, or make binding commitments without explicit approval.
