"""Shared fixtures for backend HTTP-boundary tests.

Static fixture files (Vite manifest, later RSS/HTML samples) live in tests/fixtures/.
Fake adapters get registered here as they appear.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import override_settings

from adapters.fetch import set_fetcher
from core.vite import _read_manifest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def vite_manifest() -> Iterator[None]:
    """Serve the shell against a fixed Vite manifest so tests never need a frontend build."""
    _read_manifest.cache_clear()
    with override_settings(
        VITE_DEV_SERVER_URL="", VITE_MANIFEST_PATH=FIXTURES / "vite-manifest.json"
    ):
        yield
    _read_manifest.cache_clear()


class FakeFetcher:
    """고정 응답을 URL별로 돌려주는 페처. 값이 예외면 던진다. 요청 기록을 남긴다."""

    def __init__(self, responses: dict[str, bytes | Exception] | None = None) -> None:
        self.responses: dict[str, bytes | Exception] = responses or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.requested.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def rss_bytes() -> bytes:
    return (FIXTURES / "news-feed.xml").read_bytes()


@pytest.fixture
def fake_fetcher() -> Iterator[FakeFetcher]:
    """등록 지점에 가짜 페처를 끼운다(실제 네트워크 호출 금지)."""
    fetcher = FakeFetcher()
    previous = set_fetcher(fetcher)
    yield fetcher
    set_fetcher(previous)
