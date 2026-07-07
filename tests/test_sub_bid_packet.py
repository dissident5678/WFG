from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wfg_sub_bid_packet.py"
spec = importlib.util.spec_from_file_location("wfg_sub_bid_packet", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

JANITORIAL_OPP = ROOT / "opportunities" / "7ac9f9cded1a47e38a4f759e8ac4-janitorial-services-for-usda-ars-frederick-maryland"


@pytest.mark.skipif(not JANITORIAL_OPP.exists(), reason="janitorial opportunity fixture folder not present in this checkout (live-box data)")
def test_packet_for_janitorial_includes_commercial_fit_and_excludes_internal_gate_notes():
    md, data = mod.build_packet(JANITORIAL_OPP)
    assert "janitorial" in md.lower()
    assert "residential-only" in md.lower()
    assert "Prepared by Wright Foster Group LLC for subcontractor pricing review." in md
    assert "simplified subcontractor-facing" not in md.lower()
    assert "full SAM.gov solicitation package is retained internally" not in md
    assert "Gate 1" not in md
    assert "not approved" not in md
    assert data["fit_filter"]["required_fit_terms"]


def test_global_fit_rules_cover_non_janitorial_trades():
    striping = mod.derive_trade_filter("", "Parking lot restriping project", "paint pavement markings")
    assert striping["rule"] == "parking lot striping/paving"
    assert any("parking lot striping" in x.lower() for x in striping["required_fit_terms"])
    assert any("residential driveway" in x.lower() for x in striping["exclude_or_manual_review"])

    generic = mod.derive_trade_filter("- Trade/NAICS: specialty lab equipment calibration", "", "")
    assert generic["rule"] == "generic trade-specific fit"
    assert any("consumer-only" in x.lower() for x in generic["exclude_or_manual_review"])


def test_sludge_scope_uses_sludge_hauling_fit_not_janitorial():
    filters = mod.derive_trade_filter("", "Scott AFB sludge removal", "wastewater sludge pumping and hauling")
    assert filters["rule"] == "solid waste / sludge hauling"
    assert any("sludge hauling" in x.lower() for x in filters["required_fit_terms"])
    assert not any("janitorial" in x.lower() for x in filters["required_fit_terms"])


def test_tree_scope_uses_landscaping_fit_not_sludge_for_debris_hauling():
    filters = mod.derive_trade_filter("", "BEJ Hazard Trees and Vegetation Removal", "tree felling, vegetation clearing, slash/debris removal and hauling")
    assert filters["rule"] == "landscaping/grounds/tree"
    assert not any("sludge" in x.lower() for x in filters["required_fit_terms"])
