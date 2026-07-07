#!/usr/bin/env python3
"""Create the WFG Google Drive review hub bundle for one opportunity.

Phase 3 MVDE scope:
- create/find the private opportunity review folders;
- write one mobile command snapshot, overwritten in place;
- upload only review-ready packet, approval, draft-email, and snapshot files;
- record Drive links, hashes, versions, and audience labels in artifact_index.

This script never sends email, shares files publicly, submits proposals, signs,
certifies, spends money, or contacts third parties.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = Path(os.environ.get("WFG_DB_PATH", str(PROJECT / "state" / "wfg_workflow.sqlite3"))).resolve()
CONFIG = PROJECT / "config" / "drive-review-hub.json"
TOKEN = Path(os.environ.get("GOOGLE_TOKEN_PATH", "/home/nick/.hermes/google_token.json"))
FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

REVIEW_EXTENSIONS = {".docx", ".md", ".txt", ".eml", ".json", ".pdf", ".xlsx", ".csv"}
DEFAULT_MVDE_FOLDERS = [
    "00 Command Snapshot",
    "02 Internal Review",
    "03 Subcontractor Packet",
    "04 Approvals",
    "05 Draft Emails",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "default_root_folder_name": "WFG Review Hub",
        "env_var": "WFG_DRIVE_ROOT_FOLDER_ID",
        "mvde_folder_names": DEFAULT_MVDE_FOLDERS,
        "folder_tree": [],
    }


def folder_names_from_tree(config: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in config.get("folder_tree", []):
        name = str(item).rstrip("/").split("/")[-1]
        if name and name not in names:
            names.append(name)
    return names


def review_folder_names(config: dict[str, Any], *, full: bool = False) -> list[str]:
    if full:
        names = folder_names_from_tree(config)
    else:
        names = list(config.get("mvde_folder_names") or DEFAULT_MVDE_FOLDERS)
    return names or DEFAULT_MVDE_FOLDERS


def drive_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def google_drive_service() -> tuple[Any, Any]:
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Google API libraries required. Install google-api-python-client "
            "and google-auth in the Hermes environment."
        ) from exc
    creds = Credentials.from_authorized_user_file(str(TOKEN), scopes=SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False), MediaFileUpload


def drive_find_or_create_folder(drive: Any, name: str, parent_id: str | None = None) -> dict[str, Any]:
    q = f"mimeType='{FOLDER_MIME}' and name='{drive_escape(name)}' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    existing = drive.files().list(q=q, fields="files(id,name,webViewLink)", spaces="drive", pageSize=10).execute().get("files", [])
    if existing:
        return existing[0]
    body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    return drive.files().create(body=body, fields="id,name,webViewLink").execute()


def mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".txt":
        return "text/plain"
    if suffix == ".json":
        return "application/json"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".eml":
        return "message/rfc822"
    return "application/octet-stream"


def drive_upload_file(drive: Any, media_cls: Any, path: Path, folder_id: str) -> dict[str, Any]:
    media = media_cls(str(path), mimetype=mime_for(path), resumable=False)
    q = f"name='{drive_escape(path.name)}' and '{folder_id}' in parents and trashed=false"
    existing = drive.files().list(q=q, fields="files(id,name,webViewLink,mimeType)", spaces="drive", pageSize=10).execute().get("files", [])
    if existing:
        file_id = existing[0]["id"]
        return drive.files().update(fileId=file_id, media_body=media, fields="id,name,webViewLink,mimeType").execute()
    body = {"name": path.name, "parents": [folder_id]}
    return drive.files().create(body=body, media_body=media, fields="id,name,webViewLink,mimeType").execute()


def opportunity_year(opp: Path, generated_at: str | None = None) -> str:
    m = re.match(r"^(20\d{2})[-_]?\d{2}[-_]?\d{2}", opp.name)
    if m:
        return m.group(1)
    if generated_at:
        return generated_at[:4]
    return str(dt.date.today().year)


def opportunity_slug(opp: Path) -> str:
    return opp.name[:120] or "opportunity"


def ensure_opportunity_folders(drive: Any, opp: Path, config: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    env_var = config.get("env_var") or "WFG_DRIVE_ROOT_FOLDER_ID"
    root_id = os.environ.get(env_var, "").strip()
    root = {"id": root_id, "name": config.get("default_root_folder_name", "WFG Review Hub")}
    if not root_id:
        root = drive_find_or_create_folder(drive, root["name"])
        root_id = root["id"]
    sam = drive_find_or_create_folder(drive, "SAM Opportunities", root_id)
    year = drive_find_or_create_folder(drive, opportunity_year(opp), sam["id"])
    opportunity = drive_find_or_create_folder(drive, opportunity_slug(opp), year["id"])
    folders: dict[str, dict[str, Any]] = {}
    for name in review_folder_names(config, full=full):
        folders[name] = drive_find_or_create_folder(drive, name, opportunity["id"])
    return {
        "root": root,
        "sam_opportunities": sam,
        "year": year,
        "opportunity": opportunity,
        "review_folders": folders,
    }


def infer_dedupe_key(opp: Path) -> str:
    m = re.match(r"([0-9a-f]{32})-", opp.name)
    if m:
        return "notice:" + m.group(1)
    return opp.name


def table_exists(c: sqlite3.Connection, table: str) -> bool:
    row = c.execute("select name from sqlite_master where type='table' and name=?", (table,)).fetchone()
    return bool(row)


def workflow_summary(opp: Path, dedupe_key: str) -> dict[str, Any]:
    summary = {"workflow_state": "unknown", "next_gate": "unknown", "waiting_approvals": []}
    if not DB.exists():
        return summary
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        if table_exists(c, "opportunities") and dedupe_key:
            row = c.execute("select workflow_status from opportunities where dedupe_key=? limit 1", (dedupe_key,)).fetchone()
            if row and row["workflow_status"]:
                summary["workflow_state"] = row["workflow_status"]
        if table_exists(c, "workflow_tasks") and dedupe_key:
            row = c.execute(
                """select next_gate from workflow_tasks
                    where dedupe_key=? and current_state not in ('completed','cancelled','superseded')
                    order by task_id desc limit 1""",
                (dedupe_key,),
            ).fetchone()
            if row and row["next_gate"]:
                summary["next_gate"] = row["next_gate"]
        if table_exists(c, "approvals") and dedupe_key:
            rows = c.execute(
                """select approval_id, gate_id, gate, decision from approvals
                    where dedupe_key=? and decision in ('pending','draft')
                    order by id desc limit 5""",
                (dedupe_key,),
            ).fetchall()
            summary["waiting_approvals"] = [dict(r) for r in rows]
    finally:
        c.close()
    return summary


def write_command_snapshot(opp: Path, dedupe_key: str = "") -> Path:
    dedupe_key = dedupe_key or infer_dedupe_key(opp)
    snapshot_dir = opp / "00 Command Snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / "command_snapshot.md"
    generated_at = utc_now()
    summary = workflow_summary(opp, dedupe_key)
    lines = [
        "# WFG Command Snapshot",
        "",
        f"- Generated at: {generated_at}",
        f"- Opportunity: {opp.name}",
        f"- Local folder: `{opp}`",
        f"- Dedupe key: `{dedupe_key}`",
        f"- Workflow state: {summary['workflow_state']}",
        f"- Next gate: {summary['next_gate']}",
        "- External status: no outreach, sharing, submission, signing, certification, or spend is authorized by this snapshot.",
        "",
        "## Waiting Approvals",
    ]
    if summary["waiting_approvals"]:
        for item in summary["waiting_approvals"]:
            gate = item.get("gate_id") or item.get("gate") or "unknown gate"
            lines.append(f"- {item.get('approval_id') or 'approval'}: {gate} ({item.get('decision')})")
    else:
        lines.append("- None found in the local workflow database.")
    lines += [
        "",
        "## Review Files",
        "- Subcontractor packet: `subcontractor_bid_packet/subcontractor_bid_packet.docx` or `.md` when present.",
        "- Internal review: `subcontractor_bid_packet/internal_review_summary.md` when present.",
        "- Approvals: `approvals/` when present.",
        "- Draft emails: `drafts/` or `05 Draft Emails/` when present.",
        "",
        "This file is overwritten in place. History lives in `workflow_events` and `artifact_index`.",
        "",
    ]
    snapshot.write_text("\n".join(lines), encoding="utf-8")
    return snapshot


def classify_artifact(path: Path) -> dict[str, str] | None:
    name = path.name.lower()
    parts = {p.lower() for p in path.parts}
    if path.suffix.lower() not in REVIEW_EXTENSIONS:
        return None
    if name == "command_snapshot.md":
        return {"folder": "00 Command Snapshot", "artifact_type": "command_snapshot", "audience": "wfg_internal"}
    if "approvals" in parts or "approval" in name:
        return {"folder": "04 Approvals", "artifact_type": "approval_packet", "audience": "wfg_internal"}
    email_draft_name = any(token in name for token in [
        "email",
        "outreach",
        "quote_request",
        "quote-request",
        "draft_subcontractor",
        "draft-outreach",
        "draft_outreach",
    ])
    if "05 draft emails" in parts or email_draft_name:
        return {"folder": "05 Draft Emails", "artifact_type": "draft_email", "audience": "wfg_internal"}
    if "subcontractor_bid_packet" in parts:
        if name in {"subcontractor_bid_packet.docx", "subcontractor_bid_packet.md"}:
            return {"folder": "03 Subcontractor Packet", "artifact_type": "subcontractor_packet", "audience": "subcontractor_facing"}
        if name == "internal_review_summary.md":
            return {"folder": "02 Internal Review", "artifact_type": "internal_review_summary", "audience": "wfg_internal"}
        if name == "source_map.json":
            return {"folder": "02 Internal Review", "artifact_type": "source_map", "audience": "wfg_internal"}
        if name == "bid_packet_data.json":
            return {"folder": "02 Internal Review", "artifact_type": "bid_packet_data", "audience": "wfg_internal"}
        if name == "review_manifest.json":
            return {"folder": "02 Internal Review", "artifact_type": "review_manifest", "audience": "wfg_internal"}
    return None


def collect_review_artifacts(opp: Path, extra_files: list[Path] | None = None, *, dedupe_key: str = "") -> list[dict[str, Any]]:
    command_snapshot = write_command_snapshot(opp, dedupe_key)
    candidates: list[Path] = [command_snapshot]
    packet_dir = opp / "subcontractor_bid_packet"
    for rel in [
        "subcontractor_bid_packet.docx",
        "subcontractor_bid_packet.md",
        "internal_review_summary.md",
        "source_map.json",
        "bid_packet_data.json",
        "review_manifest.json",
    ]:
        candidates.append(packet_dir / rel)
    for folder in [opp / "approvals", opp / "drafts", opp / "05 Draft Emails"]:
        if folder.exists():
            candidates.extend(p for p in folder.rglob("*") if p.is_file())
    candidates.extend(extra_files or [])

    seen: set[Path] = set()
    artifacts: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        classification = classify_artifact(resolved)
        if not classification:
            continue
        digest = sha256_file(resolved)
        artifacts.append({
            **classification,
            "local_path": str(resolved),
            "name": resolved.name,
            "sha256": digest,
            "version": f"{classification['artifact_type']}-{digest[:12]}",
        })
    return artifacts


def ensure_artifact_index_schema(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS artifact_index(
          artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT,
          artifact_type TEXT NOT NULL,
          audience TEXT,
          local_path TEXT,
          drive_file_id TEXT,
          drive_web_view_link TEXT,
          version TEXT,
          sha256 TEXT,
          created_at TEXT NOT NULL,
          superseded_at TEXT,
          environment TEXT DEFAULT 'production'
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_index_opp ON artifact_index(dedupe_key, artifact_type);
        """
    )


def record_artifact_index(dedupe_key: str, artifact: dict[str, Any], upload: dict[str, Any]) -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        ensure_artifact_index_schema(c)
        existing = c.execute(
            """select artifact_id from artifact_index
                where dedupe_key=? and artifact_type=? and local_path=? and sha256=?
                  and coalesce(drive_file_id,'')=coalesce(?, '') and superseded_at is null
                order by artifact_id desc limit 1""",
            (dedupe_key, artifact["artifact_type"], artifact["local_path"], artifact["sha256"], upload.get("id")),
        ).fetchone()
        if existing:
            c.commit()
            return int(existing["artifact_id"])
        c.execute(
            """update artifact_index set superseded_at=?
                where dedupe_key=? and artifact_type=? and local_path=? and superseded_at is null""",
            (utc_now(), dedupe_key, artifact["artifact_type"], artifact["local_path"]),
        )
        cur = c.execute(
            """insert into artifact_index(
                 dedupe_key, artifact_type, audience, local_path, drive_file_id,
                 drive_web_view_link, version, sha256, created_at, environment)
               values(?,?,?,?,?,?,?,?,?,?)""",
            (
                dedupe_key,
                artifact["artifact_type"],
                artifact["audience"],
                artifact["local_path"],
                upload.get("id"),
                upload.get("webViewLink"),
                artifact["version"],
                artifact["sha256"],
                utc_now(),
                "production",
            ),
        )
        c.commit()
        return int(cur.lastrowid)
    finally:
        c.close()


def write_manifest(opp: Path, manifest: dict[str, Any]) -> Path:
    out = opp / "00 Command Snapshot" / "drive_review_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return out


def write_drive_blocker(opp: Path, error: str) -> Path:
    out = opp / "blockers" / "drive_review_hub_blocker.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join([
            "# Drive Review Hub Blocker",
            "",
            f"- Created at: {utc_now()}",
            f"- Opportunity: {opp.name}",
            f"- Error: {error}",
            "",
            "Local review artifacts were preserved. Re-run the Drive hub upload after credentials/configuration are fixed.",
            "",
        ]),
        encoding="utf-8",
    )
    return out


def upload_review_bundle(
    opp: Path,
    *,
    extra_files: list[Path] | None = None,
    dedupe_key: str = "",
    full: bool = False,
    dry_run: bool = False,
    drive: Any | None = None,
    media_cls: Any | None = None,
    config_path: Path = CONFIG,
) -> dict[str, Any]:
    opp = opp.resolve()
    dedupe_key = dedupe_key or infer_dedupe_key(opp)
    config = load_config(config_path)
    artifacts = collect_review_artifacts(opp, extra_files, dedupe_key=dedupe_key)
    result: dict[str, Any] = {
        "ok": True,
        "mode": "full" if full else "mvde",
        "dry_run": dry_run,
        "created_at": utc_now(),
        "opportunity_folder": str(opp),
        "dedupe_key": dedupe_key,
        "public_sharing": False,
        "artifacts": artifacts,
        "uploaded_files": [],
        "artifact_index_ids": [],
    }
    if dry_run:
        result["manifest_path"] = str(write_manifest(opp, result))
        return result

    if drive is None or media_cls is None:
        drive, media_cls = google_drive_service()
    folders = ensure_opportunity_folders(drive, opp, config, full=full)
    result["drive_folders"] = folders

    for artifact in artifacts:
        folder = folders["review_folders"].get(artifact["folder"])
        if not folder:
            continue
        upload = drive_upload_file(drive, media_cls, Path(artifact["local_path"]), folder["id"])
        uploaded = {**upload, "local_path": artifact["local_path"], "artifact_type": artifact["artifact_type"], "audience": artifact["audience"]}
        result["uploaded_files"].append(uploaded)
        artifact_id = record_artifact_index(dedupe_key, artifact, upload)
        result["artifact_index_ids"].append(artifact_id)

    result["manifest_path"] = str(write_manifest(opp, result))
    return result


def safe_upload_review_bundle(*args: Any, **kwargs: Any) -> dict[str, Any]:
    opp = Path(args[0] if args else kwargs.get("opp")).resolve()
    try:
        return upload_review_bundle(*args, **kwargs)
    except Exception as exc:
        blocker = write_drive_blocker(opp, str(exc))
        fallback = {
            "ok": False,
            "error": str(exc),
            "created_at": utc_now(),
            "opportunity_folder": str(opp),
            "public_sharing": False,
            "blocker_path": str(blocker),
        }
        fallback["manifest_path"] = str(write_manifest(opp, fallback))
        return fallback


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("opportunity_folder", type=Path)
    ap.add_argument("--dedupe-key", default="")
    ap.add_argument("--full", action="store_true", help="Create the full Section 6 folder tree instead of the MVDE subset.")
    ap.add_argument("--dry-run", action="store_true", help="Create local command snapshot/manifest only; do not call Google Drive.")
    ap.add_argument("--config", type=Path, default=CONFIG)
    args = ap.parse_args()

    result = safe_upload_review_bundle(
        args.opportunity_folder,
        dedupe_key=args.dedupe_key,
        full=args.full,
        dry_run=args.dry_run,
        config_path=args.config,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
