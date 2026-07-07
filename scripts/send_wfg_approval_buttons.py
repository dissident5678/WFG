#!/usr/bin/env python3
"""Send a Wright Foster Group approval request to Telegram with Approve/Deny buttons.

The script stores a short callback ID -> approval packet path mapping in
`approvals/button-registry.json`. The Hermes Telegram gateway handles callback
data shaped as `wfg:approve:<id>` and `wfg:deny:<id>`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get('WFG_PROJECT_DIR', '/home/nick/workspace/wfg-gov-contracting-v2')).resolve()
APPROVALS = WORKSPACE / 'approvals'
REGISTRY = APPROVALS / 'button-registry.json'
ENV_PATH = Path('/home/nick/.hermes/.env')
DEFAULT_CHAT_ID = '-1003889564123'
DEFAULT_THREAD_ID = '295'


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(errors='ignore').splitlines():
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            key, val = s.split('=', 1)
            env.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return env


def extract_section(text: str, heading: str, limit: int = 600) -> str:
    pattern = re.compile(rf'^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)', re.M)
    m = pattern.search(text)
    if not m:
        return ''
    section = m.group(1).strip()
    section = re.sub(r'\n{3,}', '\n\n', section)
    return section[:limit].rstrip()


def first_field(text: str, label: str) -> str:
    m = re.search(rf'^{re.escape(label)}:\s*(.+)$', text, re.M)
    return m.group(1).strip() if m else ''


def summarize_packet(packet: Path, title_override: str | None = None) -> tuple[str, str]:
    text = packet.read_text(errors='ignore')
    title = title_override or first_field(text, 'Opportunity / project') or packet.stem
    approval_type = first_field(text, 'Approval type') or 'Approval request'
    status = first_field(text, 'Current status') or 'Pending human decision'
    folder = first_field(text, 'Opportunity folder')
    action = extract_section(text, 'Exact action requiring authorization', 450)
    risks = extract_section(text, 'Important risks', 550)
    review = extract_section(text, 'Files / documents / emails / drafts Nick should review', 450)
    if not review:
        review = extract_section(text, 'Review files', 450)
    parts = [
        f'## APPROVAL NEEDED — {title}',
        f'Approval type: {approval_type}',
        f'Status: {status}',
    ]
    if folder:
        parts.append(f'Opportunity folder: {folder}')
    parts.append(f'Approval packet: `{packet}`')
    if action:
        parts.append('\n**Action needing authorization:**\n' + action)
    if risks:
        parts.append('\n**Key risks:**\n' + risks)
    if review:
        parts.append('\n**Review files:**\n' + review)
    parts.append('\nTap one button below. Button result records the decision in the WFG approval log and moves the packet out of pending. It does not by itself send emails, contact vendors, approve price, or submit a bid.')
    return title, '\n\n'.join(parts)


def telegram_request(token: str, method: str, payload: dict) -> dict:
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = urllib.parse.urlencode({'payload_json': json.dumps(payload)}).encode()
    # Telegram Bot API expects JSON body; urllib with explicit headers.
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--packet', required=True, help='Path to approval packet markdown')
    ap.add_argument('--title', help='Optional title override')
    ap.add_argument('--chat-id', default=DEFAULT_CHAT_ID)
    ap.add_argument('--thread-id', default=DEFAULT_THREAD_ID)
    args = ap.parse_args()

    env = load_env()
    token = env.get('TELEGRAM_BOT_TOKEN')
    if not token:
        print('TELEGRAM_BOT_TOKEN is not configured', file=sys.stderr)
        return 2

    packet = Path(args.packet).resolve()
    if not packet.exists():
        print(f'Approval packet not found: {packet}', file=sys.stderr)
        return 2

    approval_id = hashlib.sha256(str(packet).encode('utf-8')).hexdigest()[:12]
    title, text = summarize_packet(packet, args.title)

    APPROVALS.mkdir(parents=True, exist_ok=True)
    try:
        registry = json.loads(REGISTRY.read_text(errors='ignore')) if REGISTRY.exists() else {}
    except Exception:
        registry = {}
    registry[approval_id] = {
        'approval_id': approval_id,
        'packet_path': str(packet),
        'title': title,
        'status': 'pending',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'chat_id': args.chat_id,
        'thread_id': args.thread_id,
    }
    tmp = REGISTRY.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(REGISTRY)

    payload = {
        'chat_id': args.chat_id,
        'text': text,
        'disable_web_page_preview': True,
        'reply_markup': {
            'inline_keyboard': [
                [
                    {'text': '✅ Approve', 'callback_data': f'wfg:approve:{approval_id}'},
                    {'text': '❌ Deny', 'callback_data': f'wfg:deny:{approval_id}'},
                ],
                [
                    {'text': '✏️ Revise', 'callback_data': f'wfg:revise:{approval_id}'},
                    {'text': '⏸ Hold', 'callback_data': f'wfg:hold:{approval_id}'},
                ],
            ]
        },
    }
    if args.thread_id:
        payload['message_thread_id'] = int(args.thread_id)

    result = telegram_request(token, 'sendMessage', payload)
    print(json.dumps({
        'ok': result.get('ok'),
        'approval_id': approval_id,
        'message_id': result.get('result', {}).get('message_id'),
        'packet': str(packet),
        'title': title,
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
