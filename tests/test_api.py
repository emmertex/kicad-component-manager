from unittest.mock import MagicMock, patch

import pytest

from lib.api import JLCPCBAPIWrapper, LCSCAPIClient, fetch_component_data
from tests.conftest import ALL_LCSC_PARTS, FIXTURES
from tests.generation import load_json, parse_lcsc_from_fixture


@pytest.mark.parametrize("pid", ALL_LCSC_PARTS)
def test_lcsc_fixture_parses(pid):
    info = parse_lcsc_from_fixture(pid)
    assert info.get("value")
    assert info.get("mfr")
    assert info.get("category")
    assert info.get("package")
    assert "specifications" in info
    assert isinstance(info["specifications"], dict)


def test_lcsc_unicode_and_tolerance_fields():
    info = parse_lcsc_from_fixture("C25804")
    assert "±" in info.get("description", "") or any(
        "±" in v for v in info.get("specifications", {}).values()
    )


@pytest.mark.parametrize("pid", ("C25804", "C8734"))
def test_jlc_wrapper_maps_official_fixture(pid):
    mapped = load_json("jlc_official_mapped.json")[pid]
    comp = mapped["data"]
    pricing = mapped["pricing"]
    inventory = mapped["inventory"]

    with patch("lib.api.JLCPCBAPIClient") as mock_cls:
        client = mock_cls.return_value
        client.get_component.return_value = comp
        client.get_pricing.return_value = pricing
        client.get_inventory.return_value = inventory["stock"]

        out = JLCPCBAPIWrapper("fake-key").get_component_data(pid)

    assert out["value"] == comp["model"]
    assert out["mfr"] == comp["brand"]
    assert out["stock"]
    assert out["price"].startswith("$")
    assert "Resistance" in out["specifications"] or out["specifications"]


def test_fetch_component_data_uses_lcsc_when_jlc_unavailable():
    with patch("lib.api.JLCPCBAPIWrapper") as jlc_mock:
        jlc_mock.return_value.get_component_data.return_value = None
        with patch.object(LCSCAPIClient, "get_component_data") as lcsc_mock:
            lcsc_mock.return_value = {"value": "TEST"}
            out = fetch_component_data("C25804")
    assert out["value"] == "TEST"


def test_fetch_component_data_prefers_jlc():
    with patch("lib.api.JLCPCBAPIWrapper") as jlc_mock:
        jlc_mock.return_value.get_component_data.return_value = {"value": "JLC"}
        out = fetch_component_data("C25804", api_key="key")
    assert out["value"] == "JLC"


def test_jlc_cart_fixture_has_real_data():
    cart = load_json("jlc_cart_responses.json")
    data = cart["C25804"]["body"]["data"]
    assert data["componentCode"] == "C25804"
    assert data["componentModelEn"] == "0603WAF1002T5E"
    assert data["attributes"]
