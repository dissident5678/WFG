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
- Phase 2 (gate IDs, ledger, migration, tests): executed 2026-07-07 —
  `scripts/wfg_gates.py` registry, dispatcher matches gate_id only and refuses
  unknowns, DB-first `workflow_tasks` queue with tolerant Kanban mirror,
  Revise/Hold buttons, `external_action_ledger` + `artifact_index` tables,
  `scripts/wfg_state_migration.py` applied to the extracted DB (backup in
  `state/backups/`; 8 states migrated, 24 historical contacts backfilled, 23
  gate IDs assigned). Test suite: 41 passed, 6 environment-skipped.
- Next: MVDE subsets of Phase 3 (Drive review bundle upload) and Phase 5
  (GATE_2_PACKAGE/GATE_2_SEND cycle; send worker last, ledger-checked).
  Deployment to the live box must re-run the migration there (idempotent) and
  update the Telegram callback handler to record revise/hold button presses.
