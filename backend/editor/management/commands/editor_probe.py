"""손으로 돌리는 실동작 확인(설계 §8). 자동 시험·CI·스케줄러는 이 명령을 부르지 않는다.

로컬 DB의 미처리 수집물 몇 건으로 단계 하나를 **실제 모델**에 보내고 스키마 통과·검증 결과·토큰·
추정 비용을 출력한다. 아무것도 저장하지 않는다(EditorRun·EditorCall도 만들지 않는다).
`--stage write`는 분류를 한 번 부르고 그 판단의 새 기사·갱신 앞 `--writes`건을 작성까지 부른다
(원문은 실제로 받는다). `--stage commentary --article <id>`는 로컬 DB의 기사 하나로 3단계 분석을
한 번 부른다(추천·분석 여부는 따지지 않는다 — 확인용). 포스트 링크는 그 기사 slug로 붙는다.
키는 `set -a; source ~/.config/sandcorp/news-claude.env; set +a`로 환경에만 올린다.
"""

import json
from collections.abc import Callable
from functools import partial
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from adapters.anthropic import LlmClient, LlmNotConfigured, get_llm
from adapters.clock import get_clock
from adapters.fetch import get_fetcher
from articles.models import Article, ArticleItem
from services import editor_classify, editor_commentary, editor_write, llm_pricing, runtime_settings
from services.editor_pipeline import Outcome, classify, commentary, write
from services.editor_prompts import parse_effort


class Command(BaseCommand):
    help = "실제 API로 편집 단계 하나를 소량 확인한다(저장 없음). 비용이 든다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--stage", choices=["classify", "write", "commentary"], default="classify"
        )
        parser.add_argument("--article", type=int, default=0, help="commentary: 기사 id")
        parser.add_argument("--writes", type=int, default=2, help="write: 작성 호출 수(기본 2)")
        parser.add_argument("--items", type=int, default=5, help="수집물 수(기본 5)")
        parser.add_argument("--model", default="", help="기본은 EDITOR_MODEL_CLASSIFY")
        parser.add_argument("--effort", default="", help="기본은 EDITOR_EFFORT_CLASSIFY")
        parser.add_argument("--show", action="store_true", help="검증한 판단 JSON을 출력")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        stage = options["stage"]
        if stage == "commentary":
            self._commentary(options)
            return
        model = runtime_settings.EDITOR_MODEL_CLASSIFY.get()
        effort = runtime_settings.EDITOR_EFFORT_CLASSIFY.get()
        write_model = options["model"] or runtime_settings.EDITOR_MODEL_WRITE.get()
        write_effort = options["effort"] or runtime_settings.EDITOR_EFFORT_WRITE.get()
        if stage == "classify":
            model, effort = options["model"] or model, options["effort"] or effort
        try:
            for name in (model, write_model):
                llm_pricing.check_model(name)
            parse_effort(effort)
            parse_effort(write_effort)
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
        self._report(outcome)
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
        if stage == "write" and outcome.decision is not None:
            self._write(llm, outcome, data, write_model, write_effort, options)

    def _commentary(self, options: dict[str, Any]) -> None:  # noqa: ANN401 - Django 명령 옵션
        model = options["model"] or runtime_settings.EDITOR_MODEL_COMMENTARY.get()
        effort = options["effort"] or runtime_settings.EDITOR_EFFORT_COMMENTARY.get()
        try:
            llm_pricing.check_model(model)
            parse_effort(effort)
            llm = get_llm()
        except (llm_pricing.UnknownModel, ValueError, LlmNotConfigured) as error:
            raise CommandError(str(error)) from None
        article = Article.objects.select_related("category").filter(pk=options["article"]).first()
        if article is None:
            raise CommandError("--article로 로컬 DB의 기사 id를 준다")
        item_ids = list(
            ArticleItem.objects.filter(article=article)
            .order_by("id")
            .values_list("item_id", flat=True)
        )
        plan: editor_commentary.Plan = {
            "kind": "update",
            "write_key": "probe",
            "article_id": article.pk,
            "category": article.category.slug,
            "title": article.title,
            "summary": article.summary,
            "slug": article.slug,
            "item_ids": item_ids,
        }
        reader = editor_write.RunReader(get_fetcher())
        data = editor_commentary.prepare(plan, reader, get_clock().now())
        self.stdout.write(
            f"단계 commentary / 모델 {model} / effort {effort} / 기사 {article.pk} / "
            f"원문 길이 {[len(i['text']) for i in data['items']]} / "
            f"관련 기사 {len(data['related'])}건"
        )
        outcome = commentary(llm, data, plan, model, effort)
        self._report(outcome)
        if options["show"] and outcome.output is not None:
            self.stdout.write(json.dumps(outcome.output, ensure_ascii=False, indent=2))

    def _report(self, outcome: Outcome) -> None:
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

    def _write(
        self,
        llm: LlmClient,
        classified: Outcome,
        data: editor_classify.ClassifyInput,
        model: str,
        effort: str,
        options: dict[str, Any],  # noqa: ANN401 - Django 명령 옵션
    ) -> None:
        decision = classified.decision
        assert decision is not None
        reader = editor_write.RunReader(get_fetcher())
        names = {c["slug"]: c["name"] for c in data["categories"]}
        jobs: list[Callable[[], editor_write.Prepared]] = [
            partial(editor_write.prepare_new, e, names, reader) for e in decision["new_articles"]
        ]
        jobs += [partial(editor_write.prepare_update, u, reader) for u in decision["updates"]]
        total = classified.cost_usd
        for n, job in enumerate(jobs[: options["writes"]], start=1):
            one = job()  # 쓸 것만 원문을 받는다
            texts = [len(i["text"]) for i in one["data"]["items"]]
            self.stdout.write(
                f"--- 작성 {n} ({one['data']['kind']}) / 모델 {model} / effort {effort} / "
                f"원문 길이 {texts} / 페치 누계 {reader.fetches}"
            )
            outcome = write(llm, one, model, effort)
            total += outcome.cost_usd
            self._report(outcome)
            for problem in outcome.problems or []:
                self.stdout.write(f"검증 고침: {problem}")
            if options["show"] and outcome.output is not None:
                self.stdout.write(json.dumps(outcome.output, ensure_ascii=False, indent=2))
        self.stdout.write(f"합계 추정 US${total}")
