from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "wfg_email_response_assistant.py"
spec = importlib.util.spec_from_file_location("wfg_email_response_assistant", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GENERIC = "Thank you for the information. We received it and are reviewing the scope, pricing, schedule, assumptions, exclusions, and any compliance requirements before responding substantively."


def test_marketing_bank_email_is_junk_even_if_bank_keyword():
    meta = {
        "labelIds": [],
        "payload": {"headers": [
            {"name": "From", "value": "Truist | Small Business <truist@mail.mktg.truist.com>"},
            {"name": "Subject", "value": "Optimize your online business banking"},
            {"name": "List-Unsubscribe", "value": "<mailto:unsubscribe@example.com>"},
        ]},
    }
    classification, reason = mod.classify(meta, "Improve your banking experience. View in browser.")
    assert classification == "junk_or_ad"
    assert "marketing" in reason or "advertising" in reason


def test_decline_response_is_specific_not_generic():
    body = mod.draft_body(
        "Holly Smith",
        "Re: The Cleaning Authority",
        "Hi Nick, unfortunately we cannot help with this project because we only handle in-home cleaning estimates.",
        "business keyword",
    )
    assert GENERIC not in body
    assert "Hi Holly," in body
    assert "not a fit" in body or "not available" in body
    assert "in-home" in body or "residential" in body


def test_quote_response_references_amount_and_question():
    body = mod.draft_body(
        "Real Clean Estimating",
        "Janitorial quote",
        "Attached is our quote for $27,450. Can the site visit happen before Friday? Exclusions are listed on page 2.",
        "business keyword",
    )
    assert GENERIC not in body
    assert "$27,450" in body
    assert "site visit" in body
    assert "Question noted" in body or "noted your question" in body


def test_generic_fallback_quotes_inbound_excerpt():
    body = mod.draft_body("", "Update", "We changed our office hours next week and wanted you to know.", "direct question")
    assert GENERIC not in body
    assert "We changed our office hours" in body
    assert "generic acknowledgement" in body
