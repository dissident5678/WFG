#!/usr/bin/env python3
"""Repository hardening checks before GitHub push or Hermes laptop rebuild."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("google_token", re.compile(r'"refresh_token"\s*:\s*"[^"]+"')),
    ("secret_assignment", re.compile(r"(?im)^\s*[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*['\"][^'\"\n]{16,}['\"]")),
    ("telegram_bot_token", re.compile(r"\b\d{7,}:[A-Za-z0-9_-]{30,}\b")),
]
SENSITIVE_TRACKED = [
    re.compile(r"(^|/)state/"),
    re.compile(r"(^|/)subcontractors/subcontractor_master\.(csv|md)$"),
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"(^|/)google_.*token.*\.json$"),
]


def git_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files"], cwd=PROJECT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return [PROJECT / line for line in proc.stdout.splitlines() if line.strip()]


def scan_file(path: Path) -> list[dict[str, Any]]:
    rel = str(path.relative_to(PROJECT))
    findings: list[dict[str, Any]] = []
    for pat in SENSITIVE_TRACKED:
        if rel == ".env.example":
            continue
        if pat.search(rel):
            findings.append({"file": rel, "kind": "sensitive_tracked_path", "detail": pat.pattern})
    if path.stat().st_size > 2_000_000:
        return findings
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings
    for name, pat in SECRET_PATTERNS:
        if pat.search(text):
            findings.append({"file": rel, "kind": name, "detail": "pattern matched"})
    return findings


def check() -> dict[str, Any]:
    files = git_files()
    findings: list[dict[str, Any]] = []
    for path in files:
        if path.is_file():
            findings.extend(scan_file(path))
    required = [
        ".github/workflows/ci.yml",
        ".github/pull_request_template.md",
        ".env.example",
        "docs/deployment/HERMES_LAPTOP_REBUILD.md",
        "DATA_CLASSIFICATION.md",
    ]
    missing = [p for p in required if not (PROJECT / p).exists()]
    return {
        "ok": not findings and not missing,
        "tracked_files": len(files),
        "findings": findings,
        "missing_required_files": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = check()
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
