"""Resolve the script and stylesheet tags the HTML shell needs from Vite."""

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from django.conf import settings

# Web fonts the first screen needs (#98): Pretendard subset 91 holds Latin + the commonest Hangul,
# 90/89/88 the next most frequent syllables, plus the mono Latin face used by labels. The rest
# of the 92 subsets load on demand through unicode-range. Matched by file-name prefix because
# Vite adds a content hash.
PRELOAD_FONT_PREFIXES = (
    "PretendardVariable.subset.91-",
    "PretendardVariable.subset.90-",
    "PretendardVariable.subset.89-",
    "PretendardVariable.subset.88-",
    "jetbrains-mono-latin-wght-normal-",
)


@dataclass(frozen=True)
class Assets:
    scripts: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
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
    emitted = chunk.get("assets", [])
    fonts = [
        f"{static}{name}"
        for prefix in PRELOAD_FONT_PREFIXES
        for name in (emitted if isinstance(emitted, list) else [])
        if isinstance(name, str) and name.rsplit("/", 1)[-1].startswith(prefix)
    ]
    return Assets(
        scripts=[f"{static}{chunk['file']}"],
        fonts=fonts,
        styles=[f"{static}{name}" for name in css] if isinstance(css, list) else [],
    )
