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
- Phase 3 MVDE subset (Drive review bundle upload): executed 2026-07-07 —
  `scripts/wfg_drive_review_hub.py` creates/finds the private Drive review
  folders, writes the single-file command snapshot, uploads review-ready packet,
  approval, and draft-email artifacts, keeps internal review files out of the
  subcontractor-facing folder, records Drive links in `artifact_index`, and has
  fake-Drive tests for idempotent folder/file behavior. Test suite: 44 passed,
  6 environment-skipped.
- Phase 4 (subagent role system): executed 2026-07-07 —
  `scripts/wfg_delegate_task.py` queues role-bound workflow tasks with mission,
  skills/SOP refs, external-action boundaries, output contract, and next gate.
  It refuses to treat a skill name as a worker.
- Phase 5 (outreach and quote collection): executed 2026-07-07 —
  `scripts/wfg_outreach_cycle.py` implements the consensus
  GATE_2_PACKAGE/GATE_2_SEND split, blocks placeholder recipients, creates
  approval packets, requires exact approved hashes before execution, records
  proof in `external_action_ledger`/`subcontractor_interactions`, and blocks
  duplicate sends. Real Gmail send requires explicit `--execute --transport
  gmail` plus `WFG_ALLOW_REAL_SEND=1`; tests use mock transport only.
- Phase 6 (proposal assembly): executed 2026-07-07 —
  `scripts/wfg_proposal_assembler.py` creates version-bound proposal packages,
  manifests, ZIP review bundles, and separate GATE_3_STRATEGY, GATE_4_PACKAGE,
  and hash-confirmed GATE_5_SUBMIT packets. Gate 4 does not imply submission.
- Phase 7 (command center): executed 2026-07-07 —
  `scripts/wfg_command_center.py` generates `latest.json`, `WFG Command
  Center.md`, and a Telegram-ready brief answering the six operator questions
  from Section 10.
- Phase 8 (hardening and GitHub review): executed 2026-07-07 —
  `.github/workflows/ci.yml`, PR checklist, `.env.example`,
  `DATA_CLASSIFICATION.md`, `docs/deployment/HERMES_LAPTOP_REBUILD.md`,
  `requirements-dev.txt`, and `scripts/wfg_repo_hardening.py` are in place.
  Verification after implementation: focused Phase 4-8 tests pass, repo
  hardening check is clean.

Next live/laptop actions: clone/pull this repo on the Hermes laptop, configure
`.env`, install `requirements-dev.txt`, run CI-equivalent checks locally, re-run
the DB migration there (idempotent), update the Telegram callback handler for
revise/hold buttons, then test a real opportunity intake. Do not perform real
outreach until exact GATE_2_PACKAGE and GATE_2_SEND approvals exist.
