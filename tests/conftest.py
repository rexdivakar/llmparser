"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return _read_fixture("article.html")


@pytest.fixture
def listing_html() -> str:
    return _read_fixture("listing.html")


@pytest.fixture
def jsapp_html() -> str:
    return _read_fixture("jsapp.html")


@pytest.fixture
def minimal_article_html() -> str:
    return _read_fixture("minimal_article.html")
