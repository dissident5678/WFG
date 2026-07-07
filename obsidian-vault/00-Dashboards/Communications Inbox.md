# Communications Inbox

```dataview
TABLE company, direction, status, dedupe_key, occurred_at, subject
FROM "05-Emails"
WHERE type = "email"
SORT occurred_at DESC
```
