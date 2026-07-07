# WFG Command Center

## Active Opportunities
```dataview
TABLE status, due_date, agency, solicitation_number
FROM "01-Opportunities"
WHERE type = "opportunity" AND status != "closed"
SORT due_date ASC
```

## Recent Communications
```dataview
TABLE direction, status, company, occurred_at
FROM "05-Emails"
WHERE type = "email"
SORT occurred_at DESC
LIMIT 25
```

## Subcontractors
```dataview
TABLE primary_email, primary_phone, last_contacted_at, opportunities
FROM "03-Companies"
WHERE contains(tags, "wfg/subcontractor")
SORT last_contacted_at DESC
```
