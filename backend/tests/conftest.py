"""Shared fixtures for backend HTTP-boundary tests.

Static fixture files (Vite manifest, later RSS/HTML samples) live in tests/fixtures/.
Fake adapters get registered here as they appear.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import override_settings

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
