"""그림자 비교(설계 §9): 그림자 실행의 판단을 수집물의 지금 상태(루틴이 한 일)와 나란히 놓는다.

수집물마다 파이프라인 판단(새 기사·갱신·버림·보류)과 실제 결과를 같은 네 갈래로 맞춘다.
실제 결과는 수집물의 지금 상태로 정한다: 묶임이면 묶인 기사가 실행 시작 뒤에 생겼으면 새 기사,
그 전부터 있었으면 갱신, 버림은 버림, 미처리는 보류. 일치는 갈래가 같고, 갱신이면 대상 기사도
같을 때다(새 기사의 묶음 경계는 따지지 않는다 — 제목·요약 대조로 사람이 본다).

분석 대조(#127): 3단계 호출마다 파이프라인 분석·포스트와, 같은 기사(새 기사면 같은 수집물로 루틴이
만든 기사, 갱신이면 대상 기사)의 루틴 분석·카카오 포스트 초안을 칸별로 나란히 놓는다.

읽기만 한다. admin 「편집자 실행」 상세의 「그림자 비교」 화면이 부른다.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, TypedDict

from articles.models import Article, Commentary
from collection.models import Item
from editor.models import EditorCall, EditorRun
from posts.models import Post

Kind = Literal["new", "update", "discard", "hold"]
KIND_LABELS: dict[Kind, str] = {
    "new": "새 기사",
    "update": "갱신",
    "discard": "버림",
    "hold": "보류",
}


class ArticleRef(TypedDict):
    id: int
    title: str
    slug: str


class RoutineArticle(ArticleRef):
    summary: str


@dataclass
class Row:
    item_id: int
    title: str
    pipeline: Kind
    pipeline_reason: str
    pipeline_target: str  # 새 기사면 파이프라인 제목(「새 기사 N」), 갱신이면 대상 기사 제목
    actual: Kind
    actual_article: ArticleRef | None
    match: bool

    @property
    def pipeline_label(self) -> str:
        return KIND_LABELS[self.pipeline]

    @property
    def actual_label(self) -> str:
        return KIND_LABELS[self.actual]


@dataclass
class NewPair:
    """파이프라인 새 기사 하나와, 그 수집물로 루틴이 만든 기사들."""

    number: int
    item_ids: list[int]
    category: str
    title: str
    summary: str
    published: bool
    featured: bool
    status: str  # 작성 호출 상태(없으면 빈 값)
    routine: list[RoutineArticle] = field(default_factory=list)


@dataclass
class Section:
    label: str
    pipeline: str
    routine: str


@dataclass
class CommentaryPair:
    """3단계 호출 하나와 같은 기사의 루틴 분석."""

    key: str
    kind: str  # 「새 기사」·「갱신」
    title: str
    status: str
    error: str
    routine_article: ArticleRef | None
    routine_hidden: bool
    sections: list[Section] = field(default_factory=list)


SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("background", "배경"),
    ("significance", "의미"),
    ("outlook_bull", "낙관 관점"),
    ("outlook_bear", "회의 관점"),
    ("watchpoints", "지켜볼 신호"),
    ("opinion", "의견"),
    ("predictions", "예측"),
    ("post", "포스트 초안"),
)


@dataclass
class Comparison:
    run: EditorRun
    rows: list[Row] = field(default_factory=list)
    pairs: list[NewPair] = field(default_factory=list)
    routine_only: list[ArticleRef] = field(default_factory=list)
    commentaries: list[CommentaryPair] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)
    calls: int = 0

    @property
    def matched(self) -> int:
        return sum(r.match for r in self.rows)

    @property
    def match_rate(self) -> float | None:
        """판단 일치율(%, 소수 1자리). 비교할 수집물이 없으면 None."""
        return round(self.matched * 100 / len(self.rows), 1) if self.rows else None

    @property
    def pipeline_only(self) -> int:
        """파이프라인만 만든 기사 수: 묶인 수집물 중 하나도 루틴 새 기사가 되지 않은 새 기사."""
        return sum(not p.routine for p in self.pairs)


def _ref(article: Article) -> ArticleRef:
    return {"id": article.pk, "title": article.title, "slug": article.slug}


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[dict[str, object]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _ints(value: object) -> list[int]:
    return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []


def compare(run: EditorRun) -> Comparison:
    result = Comparison(run=run, cost_usd=run.cost_usd, calls=run.calls.count())
    classify = run.calls.filter(key=f"r{run.pk}-classify").first()
    if classify is None or classify.output is None:
        return result
    decision = _object(classify.output.get("decision"))

    planned: dict[int, tuple[Kind, str, int | None, int | None]] = {}  # 판단, 까닭, 갱신 대상, 새 N
    for n, entry in enumerate(_list(decision.get("new_articles")), start=1):
        for item_id in _ints(entry.get("item_ids")):
            planned[item_id] = ("new", str(entry.get("reason", "")), None, n)
    for entry in _list(decision.get("updates")):
        target = entry.get("article_id")
        for item_id in _ints(entry.get("item_ids")):
            planned[item_id] = ("update", str(entry.get("reason", "")), _int(target), None)
    singles: tuple[tuple[str, Kind], ...] = (("discards", "discard"), ("holds", "hold"))
    for key, kind in singles:
        for entry in _list(decision.get(key)):
            single = _int(entry.get("item_id"))
            if single is not None:
                planned[single] = (kind, str(entry.get("reason", "")), None, None)

    written: dict[tuple[int, ...], EditorCall] = {
        tuple(call.item_ids): call for call in run.calls.filter(stage=EditorCall.Stage.WRITE_NEW)
    }
    items = Item.objects.filter(pk__in=list(planned)).select_related("article_link__article")
    by_id = {item.pk: item for item in items}
    targets = Article.objects.in_bulk([t for _, _, t, _ in planned.values() if t is not None])

    pairs: dict[int, NewPair] = {}
    for n, entry in enumerate(_list(decision.get("new_articles")), start=1):
        ids = _ints(entry.get("item_ids"))
        call = written.get(tuple(ids))
        written_article = _object(_object(call.output if call else None).get("article"))
        pairs[n] = NewPair(
            number=n,
            item_ids=ids,
            category=str(entry.get("category", "")),
            title=str(written_article.get("title", "")),
            summary=str(written_article.get("summary", "")),
            published=bool(written_article.get("published", entry.get("published"))),
            featured=bool(written_article.get("featured", entry.get("featured"))),
            status=call.get_status_display() if call else "",
        )

    routine_new: dict[int, tuple[Article, set[int]]] = {}
    for item_id, (kind, reason, target, number) in planned.items():
        item = by_id.get(item_id)
        if item is None:
            continue
        actual: Kind
        actual_article: Article | None = None
        if item.status == Item.Status.BUNDLED and hasattr(item, "article_link"):
            actual_article = item.article_link.article
            actual = "new" if actual_article.created_at >= run.started_at else "update"
        elif item.status == Item.Status.DISCARDED:
            actual = "discard"
        else:
            actual = "hold"
        if actual == "new" and actual_article is not None:
            entry_ = routine_new.setdefault(actual_article.pk, (actual_article, set()))
            entry_[1].add(item_id)
        if kind == "new" and number is not None:
            pipeline_target = pairs[number].title or f"새 기사 {number}"
        elif kind == "update" and target is not None:
            pipeline_target = targets[target].title if target in targets else f"기사 {target}"
        else:
            pipeline_target = ""
        match = kind == actual and (
            kind != "update" or (actual_article is not None and actual_article.pk == target)
        )
        result.rows.append(
            Row(
                item_id=item_id,
                title=item.title,
                pipeline=kind,
                pipeline_reason=reason,
                pipeline_target=pipeline_target,
                actual=actual,
                actual_article=_ref(actual_article) if actual_article else None,
                match=match,
            )
        )
    result.rows.sort(key=lambda r: r.item_id)

    pipeline_new_items = {i for p in pairs.values() for i in p.item_ids}
    for article, item_ids in routine_new.values():
        touched = [p for p in pairs.values() if item_ids & set(p.item_ids)]
        for pair in touched:
            pair.routine.append({**_ref(article), "summary": article.summary})
        if not item_ids & pipeline_new_items:
            result.routine_only.append(_ref(article))
    result.pairs = list(pairs.values())
    result.commentaries = _commentaries(run, result.pairs)
    return result


def _flat(value: object) -> str:
    """분석 칸 하나를 글로: 문자열 그대로, 목록은 줄마다(예측은 「claim (기간) — basis」)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines: list[str] = []
        for entry in value:
            if isinstance(entry, dict):
                claim, horizon = entry.get("claim", ""), entry.get("horizon", "")
                lines.append(f"{claim} ({horizon}) — {entry.get('basis', '')}")
            else:
                lines.append(str(entry))
        return "\n".join(lines)
    return ""


def _routine_fields(article: Article | None) -> tuple[dict[str, str], bool]:
    if article is None:
        return {}, False
    found = Commentary.objects.filter(article=article).first()
    fields: dict[str, str] = {}
    if found is not None:
        for key, _ in SECTION_LABELS[:-1]:
            fields[key] = _flat(getattr(found, key))
    post = Post.objects.filter(article=article, channel=Post.Channel.KAKAO).first()
    if post is not None:
        fields["post"] = post.text
    return fields, bool(found is not None and found.hidden)


def _commentaries(run: EditorRun, pairs: list[NewPair]) -> list[CommentaryPair]:
    calls = run.calls.filter(stage=EditorCall.Stage.COMMENTARY).order_by("id")
    by_items = {tuple(p.item_ids): p for p in pairs}
    found: list[CommentaryPair] = []
    for call in calls:
        plan = _object(_object(call.input).get("plan"))
        kind = str(plan.get("kind", ""))
        routine: Article | None = None
        if kind == "update" and call.article_id is not None:
            routine = Article.objects.filter(pk=call.article_id).first()
        elif kind == "new":
            pair = by_items.get(tuple(call.item_ids))
            ids = [r["id"] for r in pair.routine] if pair else []
            candidates = list(Article.objects.filter(pk__in=ids).order_by("id"))
            routine = next(
                (a for a in candidates if Commentary.objects.filter(article=a).exists()),
                candidates[0] if candidates else None,
            )
        output = _object(call.output)
        mine = {k: _flat(v) for k, v in _object(output.get("commentary")).items()}
        mine["post"] = str(_object(output.get("post")).get("text", ""))
        theirs, hidden = _routine_fields(routine)
        found.append(
            CommentaryPair(
                key=call.key,
                kind="갱신" if kind == "update" else "새 기사",
                title=str(plan.get("title", "")),
                status=call.get_status_display(),
                error=call.error,
                routine_article=_ref(routine) if routine else None,
                routine_hidden=hidden,
                sections=[
                    Section(label, mine.get(key, ""), theirs.get(key, ""))
                    for key, label in SECTION_LABELS
                ],
            )
        )
    return found


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None
