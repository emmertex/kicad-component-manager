"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Parts with captured EasyEDA + LCSC data
PARTS_WITH_CAD = ("C25804", "C49678", "C8734")
ALL_LCSC_PARTS = tuple(json.loads((FIXTURES / "lcsc_api_responses.json").read_text()).keys())


@pytest.fixture
def tmp_library(tmp_path):
    """Empty library output directory."""
    return tmp_path


@pytest.fixture(params=PARTS_WITH_CAD)
def part_with_cad(request):
    return request.param
