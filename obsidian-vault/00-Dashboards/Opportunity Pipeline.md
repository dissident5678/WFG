# Opportunity Pipeline

```dataview
TABLE status, due_date, agency, naics, psc
FROM "01-Opportunities"
WHERE type = "opportunity"
SORT due_date ASC
```
