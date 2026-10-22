"""Resolve the script and stylesheet tags the HTML shell needs from Vite."""

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class Assets:
    scripts: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    dev_server: str = ""


@cache
def _read_manifest(path: Path) -> dict[str, dict[str, object]]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, dict[str, object]] = json.load(fh)
    return data


def entry_assets() -> Assets:
    if settings.VITE_DEV_SERVER_URL:
        base = settings.VITE_DEV_SERVER_URL
        return Assets(
            scripts=[f"{base}/@vite/client", f"{base}/{settings.VITE_ENTRY}"],
            dev_server=base,
        )
    manifest = _read_manifest(Path(settings.VITE_MANIFEST_PATH))
    chunk = manifest[settings.VITE_ENTRY]
    static = settings.STATIC_URL
    css = chunk.get("css", [])
    return Assets(
        scripts=[f"{static}{chunk['file']}"],
        styles=[f"{static}{name}" for name in css] if isinstance(css, list) else [],
    )
