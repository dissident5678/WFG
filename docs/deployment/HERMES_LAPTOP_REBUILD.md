# Hermes Laptop Rebuild From GitHub

Use this on the Hermes laptop after pushing this repository.

## 1. Clone Or Refresh

```bash
mkdir -p /home/nick/workspace
cd /home/nick/workspace
git clone git@github.com:dissident5678/WFG.git wfg-gov-contracting-v2
cd wfg-gov-contracting-v2
```

If the repo already exists:

```bash
cd /home/nick/workspace/wfg-gov-contracting-v2
git pull --ff-only origin main
```

## 2. Configure Environment

```bash
cp .env.example .env
```

Fill in local paths and credentials on the laptop. Do not commit `.env`.

## 3. Install Test Dependencies

```bash
python3 -m pip install -r requirements-dev.txt
```

## 4. Verify The Clone

```bash
python3 -m py_compile scripts/*.py
python3 -m pytest
python3 scripts/wfg_repo_hardening.py --json
```

## 5. Migrate The Live DB

Run this only on the laptop/live box with the correct `WFG_DB_PATH`:

```bash
python3 scripts/wfg_state_migration.py --dry-run --json
python3 scripts/wfg_state_migration.py --apply --json
```

The migration is idempotent. Confirm the second dry-run is a no-op.

## 6. Update Hermes Runtime

- Point `.hermes` cron/wrappers at `/home/nick/workspace/wfg-gov-contracting-v2`.
- Ensure the scheduled approval job runs `scripts/wfg_workflow_pump.py`, not only the button reconciler.
- Update the Telegram callback handler so it records:
  - `wfg:approve:<id>`
  - `wfg:deny:<id>`
  - `wfg:revise:<id>`
  - `wfg:hold:<id>`

## 7. Operational Smoke Test

```bash
python3 scripts/wfg_workflow_pump.py --no-kanban-dispatch
python3 scripts/wfg_command_center.py
```

Do not run any real outreach send until a real `GATE_2_PACKAGE` and `GATE_2_SEND`
approval have been recorded for the exact packet, recipients, message, and hash.
