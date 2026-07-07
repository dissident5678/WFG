#!/usr/bin/env python3
"""Build a static WFG dashboard suitable for Cloudflare Pages.

Output is plain HTML/CSS/JS with a data.json file; no server required.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

PROJECT = Path(os.environ.get("WFG_PROJECT_DIR", "/home/nick/workspace/wfg-gov-contracting-v2")).resolve()
DB = PROJECT / "state" / "wfg_workflow.sqlite3"
OUT = PROJECT / "cloudflare-dashboard"
DESKTOP = Path.home() / "Desktop"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def con() -> sqlite3.Connection:
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c


def rows(c: sqlite3.Connection, q: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in c.execute(q, params)]
    except sqlite3.OperationalError:
        return []


def gather() -> dict[str, Any]:
    c = con()
    data = {
        "generated_at": now(),
        "opportunities": rows(c, """
            select oi.dedupe_key, oi.opportunity_folder, coalesce(o.title, oi.dedupe_key) title,
                   o.agency, o.solicitation_number, o.response_deadline due_date,
                   coalesce(o.workflow_status, oi.status) status, o.naics, '' psc
            from opportunity_intakes oi left join opportunities o on o.dedupe_key=oi.dedupe_key
            order by oi.id desc limit 250
        """),
        "subcontractors": rows(c, """
            select s.id, s.legal_name, s.website, group_concat(distinct cc.email) emails, group_concat(distinct cc.phone) phones,
                   group_concat(distinct l.dedupe_key) opportunity_keys, group_concat(distinct l.status) statuses,
                   max(l.last_seen) last_seen
            from subcontractors s
            left join subcontractor_contacts cc on cc.subcontractor_id=s.id
            left join subcontractor_opportunity_links l on l.subcontractor_id=s.id
            group by s.id order by max(l.last_seen) desc, s.legal_name
        """),
        "communications": rows(c, """
            select i.id, i.dedupe_key, i.interaction_type, i.status, i.direction, i.occurred_at, i.subject,
                   i.gmail_message_id, i.gmail_thread_id, i.external_id, i.match_method, s.legal_name company
            from subcontractor_interactions i left join subcontractors s on s.id=i.subcontractor_id
            order by datetime(coalesce(i.occurred_at,'1970-01-01')) desc, i.id desc limit 500
        """),
        "inbound": rows(c, """
            select gmail_message_id, thread_id, sender_email, subject, received_at, classification, reason, dedupe_key,
                   subcontractor_id, match_method, draft_id
            from email_response_items order by id desc limit 250
        """),
        "metrics": {},
    }
    data["metrics"] = {
        "opportunities": len(data["opportunities"]),
        "subcontractors": len(data["subcontractors"]),
        "communications": len(data["communications"]),
        "unmatched_inbound": len([x for x in data["inbound"] if not x.get("dedupe_key")]),
        "bounced_or_failed": len([x for x in data["communications"] if "bounce" in (x.get("status") or "").lower() or "fail" in (x.get("status") or "").lower()]),
    }
    c.close(); return data


def write_dashboard(out: Path = OUT) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    data = gather()
    (out / "data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (out / "_headers").write_text("/*\n  Cache-Control: no-store\n", encoding="utf-8")
    (out / "README.md").write_text("# WFG Static Dashboard\n\nUpload this folder, or the generated ZIP contents, to Cloudflare Pages.\n", encoding="utf-8")
    (out / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return {"ok": True, "out": str(out), "files": [str(p) for p in out.iterdir()]}


def zip_dashboard(out: Path = OUT, dest: Path | None = None) -> dict[str, Any]:
    write_dashboard(out)
    DESKTOP.mkdir(parents=True, exist_ok=True)
    dest = dest or DESKTOP / "wfg-cloudflare-dashboard.zip"
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(out))
    return {"ok": True, "zip": str(dest), "size_bytes": dest.stat().st_size, "source_dir": str(out)}


INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>WFG Operations Dashboard</title>
<style>
:root{--bg:#0f172a;--panel:#111827;--card:#1f2937;--text:#e5e7eb;--muted:#9ca3af;--accent:#38bdf8;--bad:#fb7185;--good:#34d399;--warn:#fbbf24}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}header{padding:28px 22px;background:linear-gradient(135deg,#111827,#0f172a 70%);border-bottom:1px solid #334155}h1{margin:0;font-size:28px}p{color:var(--muted)}main{padding:22px;max-width:1400px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.card{background:var(--card);border:1px solid #374151;border-radius:14px;padding:16px;box-shadow:0 8px 30px #0003}.metric{font-size:34px;font-weight:800;color:var(--accent)}section{margin-top:24px}h2{font-size:20px;margin:0 0 12px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}input{background:#020617;color:var(--text);border:1px solid #475569;border-radius:10px;padding:10px;min-width:280px}table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:12px;overflow:hidden}th,td{text-align:left;padding:10px;border-bottom:1px solid #334155;vertical-align:top}th{background:#0b1220;color:#cbd5e1;font-size:13px;text-transform:uppercase;letter-spacing:.04em}td{font-size:14px}.pill{display:inline-block;border-radius:999px;padding:3px 8px;font-size:12px;background:#334155;color:#e2e8f0}.bad{background:#7f1d1d;color:#fecaca}.good{background:#064e3b;color:#a7f3d0}.warn{background:#78350f;color:#fde68a}.muted{color:var(--muted)}a{color:var(--accent)}.small{font-size:12px}.scroll{overflow:auto;max-height:520px;border-radius:12px}</style>
</head>
<body>
<header><h1>Wright Foster Group Operations Dashboard</h1><p id="generated">Loading…</p></header>
<main>
  <div class="toolbar"><input id="filter" placeholder="Filter companies, opportunities, subjects…" /></div>
  <div class="grid" id="metrics"></div>
  <section><h2>Recent Communications</h2><div class="scroll"><table id="communications"></table></div></section>
  <section><h2>Subcontractor CRM</h2><div class="scroll"><table id="subcontractors"></table></div></section>
  <section><h2>Recent Opportunities</h2><div class="scroll"><table id="opportunities"></table></div></section>
  <section><h2>Inbound Email Matching</h2><div class="scroll"><table id="inbound"></table></div></section>
</main>
<script>
let DATA=null, filter='';
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function pill(s){let c='pill'; let x=String(s||'').toLowerCase(); if(x.includes('bounce')||x.includes('fail')||x.includes('unmatched')) c+=' bad'; else if(x.includes('sent')||x.includes('submitted')||x.includes('received')) c+=' good'; else if(x.includes('pending')||x.includes('draft')) c+=' warn'; return `<span class="${c}">${esc(s||'')}</span>`}
function match(obj){return !filter || JSON.stringify(obj).toLowerCase().includes(filter.toLowerCase())}
function fmt(col,val,row){if(col[2]==='pill')return pill(val); if(typeof col[2]==='function')return col[2](val,row); return esc(val)}
function table(id, cols, rows){rows=rows.filter(match); document.getElementById(id).innerHTML='<thead><tr>'+cols.map(c=>`<th>${esc(c[0])}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${fmt(c,r[c[1]],r)}</td>`).join('')+'</tr>').join('')+'</tbody>'}
function render(){document.getElementById('generated').textContent='Generated at '+DATA.generated_at+' UTC — static snapshot for Cloudflare Pages'; document.getElementById('metrics').innerHTML=Object.entries(DATA.metrics).map(([k,v])=>`<div class="card"><div class="metric">${esc(v)}</div><div class="muted">${esc(k.replaceAll('_',' '))}</div></div>`).join(''); table('communications',[['When','occurred_at'],['Company','company'],['Direction','direction'],['Type','interaction_type'],['Status','status','pill'],['Opportunity','dedupe_key'],['Subject','subject']],DATA.communications); table('subcontractors',[['Company','legal_name'],['Email(s)','emails'],['Phone(s)','phones'],['Status','statuses','pill'],['Opportunity Keys','opportunity_keys'],['Last Seen','last_seen']],DATA.subcontractors); table('opportunities',[['Title','title'],['Agency','agency'],['Solicitation','solicitation_number'],['Due','due_date'],['Status','status','pill'],['Dedupe','dedupe_key']],DATA.opportunities); table('inbound',[['Received','received_at'],['Sender','sender_email'],['Subject','subject'],['Class','classification','pill'],['Match','match_method','pill'],['Opportunity','dedupe_key'],['Draft','draft_id']],DATA.inbound)}
fetch('data.json',{cache:'no-store'}).then(r=>r.json()).then(d=>{DATA=d;render()}); document.getElementById('filter').addEventListener('input',e=>{filter=e.target.value;render()});
</script>
</body>
</html>
'''


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    a = sub.add_parser("zip"); a.add_argument("--dest", default="")
    args = p.parse_args()
    if args.cmd == "build": print(json.dumps(write_dashboard(), indent=2)); return 0
    if args.cmd == "zip": print(json.dumps(zip_dashboard(dest=Path(args.dest) if args.dest else None), indent=2)); return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
