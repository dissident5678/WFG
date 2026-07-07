# WFG Strategy Documents

## Authoritative operating plan

```text
docs/strategy/WFG_HERMES_DIGITAL_EMPLOYEE_CONSENSUS_PLAN.md
```

This is the single authoritative operating plan for the WFG Hermes digital employee
system, produced by a structured two-agent debate that reached consensus on
2026-07-07. Hermes must treat plan Section 12 (Replacement Instructions) as current
authority. If anything elsewhere in the repo conflicts with the plan, the plan wins.

Key sections for operators:

- Section 3 — daily operating loop and intake
- Section 5 — workflow handoffs, task states, migration spec
- Section 7 — approval gates (gate IDs, mobile flow)
- Section 11 — implementation roadmap, MVDE scope, acceptance criteria
- Section 12 — replacement instructions (what old behavior is deprecated)

## Execution status

- Phase 1 (stabilize repo): executed 2026-07-07 — paths unified behind
  `WFG_PROJECT_DIR`, skill names aligned, old instructions stubbed,
  `config/approvers.json` added.
- Phase 2 (gate IDs, ledger, migration, tests): in progress.
