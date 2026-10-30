"""단계별 시스템 프롬프트 조립(설계 §3). 편집 기준의 원천은 `automation/editor.md` 하나다.

editor.md의 `##` 절(하위 `###` 포함)을 제목으로 잘라 단계마다 `STAGE_SECTIONS`가 정한 절만
이어 붙이고, 끝에 단계 전용 지시 `automation/stages/<단계>.md`를 붙인다. 실행마다 바뀌는 것
(분야·수집물·날짜)은 넣지 않는다 — 시스템 프롬프트는 바이트 단위로 같아야 캐시가 맞는다.
"""

import hashlib
from functools import cache
from pathlib import Path
from typing import Literal

from django.conf import settings

Stage = Literal["classify", "write", "commentary"]

# 모든 단계 앞에 붙는 절. 서두는 첫 문단만 쓴다(둘째 문단은 MCP 루틴 전용).
COMMON_SECTIONS: tuple[str, ...] = ("용어", "하지 말 것")

STAGE_SECTIONS: dict[Stage, tuple[str, ...]] = {
    "classify": ("중복 판단", "분야", "게시와 추천"),
    "write": ("제목", "slug", "요약", "태그", "게시와 추천"),
    "commentary": ("AI 분석", "포스트 초안"),
}

# 파이프라인이 쓰지 않는 루틴(claude.ai 예약) 전용 절. 루틴을 끄면(#129) 지운다.
ROUTINE_ONLY_SECTIONS: tuple[str, ...] = ("도구", "실행 절차", "오류 처리")


def automation_dir() -> Path:
    return Path(settings.BASE_DIR).parent / "automation"


@cache
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_editor_md(text: str) -> tuple[str, dict[str, str]]:
    """(서두 첫 문단, 절 제목 → 본문)을 돌려준다. 본문은 `## 제목` 줄을 포함한다."""
    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        if current is None:
            if not line.startswith("# "):
                preamble_lines.append(line)
            continue
        current.append(line)
    paragraphs = [p.strip() for p in "\n".join(preamble_lines).split("\n\n") if p.strip()]
    first = paragraphs[0] if paragraphs else ""
    return first, {title: "\n".join(lines).strip() for title, lines in sections.items()}


def system_prompt(stage: Stage) -> str:
    preamble, sections = parse_editor_md(_read(automation_dir() / "editor.md"))
    titles = COMMON_SECTIONS + STAGE_SECTIONS[stage]
    missing = [t for t in titles if t not in sections]
    if missing:
        raise KeyError(f"editor.md에 없는 절: {missing}")
    parts = [
        "# 편집 기준 (automation/editor.md에서 고른 절)",
        preamble,
        *(sections[t] for t in titles),
        _read(automation_dir() / "stages" / f"{stage}.md").strip(),
    ]
    return "\n\n".join(parts) + "\n"


def prompt_version(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]
