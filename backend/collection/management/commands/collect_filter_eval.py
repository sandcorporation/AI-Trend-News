"""수집 AI 판별(#160) 오프라인 평가. DB는 읽기만 하고 아무것도 저장하지 않는다.

    manage.py collect_filter_eval --from-db --days 14 [--limit 400] [--model claude-haiku-5-5]
    manage.py collect_filter_eval --input rows.json [--limit N] [--model …]

`--input`은 `[{id, source, title, excerpt, status}]`(status `bundled`=편집자가 기사로 씀,
`discarded`=버림). 같은 프롬프트·배치(`services/collect_ai_filter.judge`)로 판정해 혼동표·비율·
비용을 낸다. 키 값은 출력하지 않는다.
"""

import json
import re
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from adapters.anthropic import LlmError, get_llm
from collection.models import Item
from services import collect_ai_filter, llm_pricing, runtime_settings

STATUSES = (Item.Status.BUNDLED.value, Item.Status.DISCARDED.value)
VERDICTS = ("in", "out", "error")
HANGUL = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Row:
    id: str
    source: str
    title: str
    excerpt: str
    status: str


def _from_db(days: int, limit: int) -> list[Row]:
    since = timezone.now() - timedelta(days=days)
    qs = (
        Item.objects.filter(status__in=STATUSES, collected_at__gte=since)
        .select_related("source")
        .order_by("-collected_at", "-id")[:limit]
    )
    return [Row(str(i.pk), i.source.name, i.title, i.excerpt, i.status) for i in qs]


def _from_file(path: str, limit: int) -> list[Row]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"입력 파일을 읽을 수 없다: {error}") from None
    if not isinstance(data, list):
        raise CommandError("입력은 [{id, source, title, excerpt, status}] 배열이어야 한다")
    rows = [
        Row(
            str(d.get("id", "")),
            str(d.get("source", "")),
            str(d.get("title", "")),
            str(d.get("excerpt", "")),
            str(d.get("status", "")),
        )
        for d in data
        if isinstance(d, dict) and d.get("status") in STATUSES
    ]
    return rows[:limit]


def _table(pairs: list[tuple[Row, collect_ai_filter.Verdict]]) -> list[str]:
    counts = {(s, v): 0 for s in STATUSES for v in VERDICTS}
    for row, verdict in pairs:
        counts[(row.status, verdict.verdict)] += 1
    lines = [f"  {'':10}" + "".join(f"{v:>7}" for v in VERDICTS)]
    for status in STATUSES:
        lines.append(f"  {status:10}" + "".join(f"{counts[(status, v)]:>7}" for v in VERDICTS))
    for status, label in (
        ("bundled", "쓴 기사(bundled)를 밖으로 판정"),
        ("discarded", "버린 것(discarded)을 밖으로 잡음"),
    ):
        total = sum(counts[(status, v)] for v in VERDICTS)
        out = counts[(status, "out")]
        rate = f"{out / total * 100:.1f}%" if total else "-"
        lines.append(f"  {label}: {out}/{total} ({rate})")
    return lines


class Command(BaseCommand):
    help = "수집 AI 판별(#160)을 지난 수집물(bundled·discarded)로 평가한다. DB 쓰기 없음."

    def add_arguments(self, parser: ArgumentParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--from-db", action="store_true", help="최근 N일 수집물을 읽는다")
        source.add_argument("--input", help="[{id, source, title, excerpt, status}] JSON 파일")
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--limit", type=int, default=400)
        parser.add_argument("--model", default="")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        model = options["model"] or runtime_settings.COLLECT_AI_FILTER_MODEL.get()
        try:
            llm_pricing.check_model(model)
        except llm_pricing.UnknownModel as error:
            raise CommandError(str(error)) from None
        limit = max(1, options["limit"])
        rows = (
            _from_file(options["input"], limit)
            if options["input"]
            else _from_db(options["days"], limit)
        )
        if not rows:
            raise CommandError("평가할 수집물(bundled·discarded)이 없다")
        try:
            llm = get_llm(
                timeout=runtime_settings.COLLECT_AI_FILTER_TIMEOUT.get(),
                max_retries=collect_ai_filter.MAX_RETRIES,
            )
        except LlmError as error:
            raise CommandError(f"LLM을 쓸 수 없다: {error}") from None
        batch = max(1, runtime_settings.COLLECT_AI_FILTER_BATCH.get())
        result = collect_ai_filter.judge(
            llm,
            [collect_ai_filter.Candidate(r.source, r.title, r.excerpt) for r in rows],
            model=model,
            categories=collect_ai_filter.category_names(),
            batch_size=batch,
        )
        pairs = list(zip(rows, result.verdicts, strict=True))
        out = self.stdout.write
        failed_calls = sum(c.status != "succeeded" for c in result.calls)
        out(f"모델 {model} · 대상 {len(rows)}건 · 요청 {len(result.calls)}회(실패 {failed_calls})")
        for label, subset in (
            ("전체", pairs),
            ("한국어", [p for p in pairs if HANGUL.search(p[0].title)]),
            ("영어", [p for p in pairs if not HANGUL.search(p[0].title)]),
        ):
            out(f"[{label}] {len(subset)}건")
            for line in _table(subset):
                out(line)
        wrong = [(r, v) for r, v in pairs if r.status == "bundled" and v.verdict == "out"]
        out(f"잘못 버린 bundled {len(wrong)}건:")
        for row, verdict in wrong:
            out(f"  - [{row.id}] {row.title} — {verdict.reason}")
        tokens_in = sum(c.input_tokens for c in result.calls)
        tokens_out = sum(c.output_tokens for c in result.calls)
        cost = sum((c.cost_usd for c in result.calls), Decimal(0))
        out(f"토큰: 입력 {tokens_in} · 출력 {tokens_out} · 비용 US${cost:.6f}")
