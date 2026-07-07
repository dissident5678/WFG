# Subcontractor CRM

```dataview
TABLE legal_name, primary_email, primary_phone, opportunities, last_contacted_at
FROM "03-Companies"
WHERE type = "company"
SORT legal_name ASC
```
