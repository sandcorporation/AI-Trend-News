"""손으로 돌리는 실동작 확인(설계 §8). 자동 시험·CI·스케줄러는 이 명령을 부르지 않는다.

로컬 DB의 미처리 수집물 몇 건으로 단계 하나를 **실제 모델**에 보내고 스키마 통과·검증 결과·토큰·
추정 비용을 출력한다. 아무것도 저장하지 않는다(EditorRun·EditorCall도 만들지 않는다).
키는 `set -a; source ~/.config/sandcorp/news-claude.env; set +a`로 환경에만 올린다.
"""

import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from adapters.anthropic import LlmNotConfigured, get_llm
from adapters.clock import get_clock
from services import editor_classify, llm_pricing
from services.editor_pipeline import classify, parse_effort


class Command(BaseCommand):
    help = "실제 API로 편집 단계 하나를 소량 확인한다(저장 없음). 비용이 든다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--stage", choices=["classify"], default="classify")
        parser.add_argument("--items", type=int, default=5, help="수집물 수(기본 5)")
        parser.add_argument("--model", default="", help="기본은 EDITOR_MODEL_CLASSIFY")
        parser.add_argument("--effort", default="", help="기본은 EDITOR_EFFORT_CLASSIFY")
        parser.add_argument("--show", action="store_true", help="검증한 판단 JSON을 출력")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        model = options["model"] or settings.EDITOR_MODEL_CLASSIFY
        effort = options["effort"] or settings.EDITOR_EFFORT_CLASSIFY
        try:
            llm_pricing.check_model(model)
            parse_effort(effort)
            llm = get_llm()
        except (llm_pricing.UnknownModel, ValueError, LlmNotConfigured) as error:
            raise CommandError(str(error)) from None
        data = editor_classify.build_input(get_clock().now(), max_items=options["items"])
        if not data["items"]:
            raise CommandError("미처리 수집물이 없다 — 로컬 DB에서 collect를 먼저 돌린다")
        outcome = classify(llm, data, model, effort)
        self.stdout.write(
            f"단계 classify / 모델 {model} / effort {effort} / 수집물 {len(data['items'])}건 / "
            f"분야 {len(data['categories'])}개 / 최근 기사 {len(data['recent_articles'])}건"
        )
        self.stdout.write(
            f"결과 {outcome.status} (재요청 {outcome.attempt}, stop_reason {outcome.stop_reason})"
            + (f" — {outcome.error}" if outcome.error else "")
        )
        self.stdout.write(
            f"토큰: 입력 {outcome.input_tokens} · "
            f"캐시 쓰기 {outcome.cache_creation_input_tokens} · "
            f"캐시 읽기 {outcome.cache_read_input_tokens} · 출력 {outcome.output_tokens} / "
            f"추정 US${outcome.cost_usd}"
        )
        if outcome.output is not None:
            decision = outcome.output["decision"]
            assert isinstance(decision, dict)
            self.stdout.write(
                "판단: "
                + ", ".join(
                    f"{k} {len(decision[k])}"
                    for k in ("new_articles", "updates", "discards", "holds")
                )
            )
            for problem in outcome.problems or []:
                self.stdout.write(f"검증 고침: {problem}")
            if options["show"]:
                self.stdout.write(json.dumps(decision, ensure_ascii=False, indent=2))
