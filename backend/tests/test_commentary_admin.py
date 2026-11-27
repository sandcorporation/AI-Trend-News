"""admin 분석 인라인(#213): 편집자 경로와 같은 검사와 예측 행 동기화를 거친다."""

import json

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from articles.models import Article, Commentary, Prediction
from services import predictions as predictions_svc
from tests.test_predictions import featured_article_id, write

pytestmark = pytest.mark.django_db

PREFIX = "commentary"


def prediction(claim: str, horizon: str = "6m", basis: str = "근거") -> dict[str, str]:
    return {"claim": claim, "horizon": horizon, "basis": basis}


def change_form_data(client: Client, article: Article) -> dict[str, str | list[str]]:
    """브라우저가 상세 화면에서 보내는 값 그대로(`initial-…` 숨은 칸 포함)."""
    page = client.get(reverse("admin:articles_article_change", args=[article.pk]))
    form = BeautifulSoup(page.content, "html.parser").select_one("#article_form")
    assert form is not None
    data: dict[str, str | list[str]] = {}
    for tag in form.select("input[name], textarea[name], select[name]"):
        name = str(tag["name"])
        if "__prefix__" in name:
            continue
        if tag.name == "textarea":
            data[name] = tag.get_text().removeprefix("\n")
        elif tag.name == "select" and tag.has_attr("multiple"):
            data[name] = [str(o["value"]) for o in tag.select("option[selected]")]
        elif tag.name == "select":
            chosen = tag.select_one("option[selected]") or tag.select_one("option")
            data[name] = str(chosen.get("value", "")) if chosen else ""
        elif tag.get("type") in ("submit", "button", "file"):
            continue
        elif tag.get("type") == "checkbox":
            if tag.has_attr("checked"):
                data[name] = str(tag.get("value", "on"))
        else:
            data[name] = str(tag.get("value", ""))
    return data


def save(
    client: Client, target: Article, /, *, add: bool = False, **inline: str
) -> tuple[int, str]:
    data = change_form_data(client, target)
    if add:  # `extra=0`: 「AI 분석 더 추가하기」를 누른 상태
        data[f"{PREFIX}-TOTAL_FORMS"] = "1"
    data |= {f"{PREFIX}-0-{name}": value for name, value in inline.items()}
    response = client.post(reverse("admin:articles_article_change", args=[target.pk]), data)
    return response.status_code, response.content.decode()


def written(*claims: tuple[str, str]) -> Article:
    article = Article.objects.get(pk=featured_article_id())
    write(article.pk, *claims)
    return article


def test_edited_claim_updates_prediction_rows(admin_client: Client) -> None:
    article = written(("맞춘 예측", "1m"), ("옛 문장", "3m"))
    judged = Prediction.objects.get(claim="맞춘 예측")
    predictions_svc.judge_prediction(judged.pk, "hit", now=timezone.now())

    status, _ = save(
        admin_client, article, predictions=json.dumps([prediction("  새 문장  ", "12m")])
    )

    assert status == 302
    assert set(Prediction.objects.values_list("claim", "verdict")) == {
        ("맞춘 예측", "hit"),  # 평가된 행 유지
        ("새 문장", "pending"),  # 미평가 행 교체(다듬은 값)
    }
    assert Commentary.objects.get().predictions == [prediction("새 문장", "12m")]
    public = admin_client.get(reverse("predictions"))
    assert "새 문장" in public.content.decode() and "옛 문장" not in public.content.decode()


def test_new_commentary_from_inline_creates_rows(admin_client: Client) -> None:
    article = Article.objects.get(pk=featured_article_id())
    text = "본문"

    status, _ = save(
        admin_client,
        article,
        add=True,
        article=str(article.pk),
        predictions=json.dumps([prediction("대표가 쓴 예측")]),
        watchpoints="",
        background=text,
        significance=text,
        outlook_bull=text,
        outlook_bear=text,
        opinion=text,
    )

    assert status == 302
    assert Prediction.objects.get().claim == "대표가 쓴 예측"
    assert Commentary.objects.get().watchpoints == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("background", "가" * 901, "배경은(는) 1~900자"),
        ("opinion", "가" * 241, "의견은(는) 1~240자"),
        ("predictions", json.dumps([prediction("가" * 121)]), "예측 문장은(는) 1~120자"),
        ("predictions", json.dumps([prediction("a", basis="가" * 301)]), "예측 근거"),
        ("predictions", json.dumps([prediction(c) for c in "abcd"]), "예측 문장은 3개까지"),
        ("predictions", json.dumps([prediction("a", "2w")]), "예측 기간은"),
        ("predictions", json.dumps([prediction("2099년까지 내린다", "1m")]), "보다 늦습니다"),
        ("predictions", json.dumps([{"claim": "기간 없음"}]), "예측 문장 형식"),
        ("predictions", json.dumps({"claim": "목록 아님"}), "예측 문장 형식"),
        ("watchpoints", json.dumps(["a", "b", "c", "d"]), "지켜볼 신호는 3개까지"),
        ("watchpoints", json.dumps([1]), "지켜볼 신호 형식"),
    ],
)
def test_violation_is_form_error_and_not_saved(
    admin_client: Client, field: str, value: str, message: str
) -> None:
    article = written(("옛 문장", "3m"))
    before = Commentary.objects.values().get()

    status, html = save(admin_client, article, add=False, **{field: value})

    assert status == 200 and escape(message) in html
    assert Commentary.objects.values().get() == before
    assert Prediction.objects.get().claim == "옛 문장"


def test_longest_allowed_basis_is_saved(admin_client: Client) -> None:
    article = written(("옛 문장", "3m"))

    status, _ = save(
        admin_client, article, predictions=json.dumps([prediction("새 문장", basis="가" * 300)])
    )

    assert status == 302
    assert Prediction.objects.get().basis == "가" * 300


def test_untouched_legacy_commentary_can_still_be_hidden(admin_client: Client) -> None:
    """검사가 생기기 전 값(#191 이전의 늦은 날짜)이 남아 있어도 숨기기는 막지 않는다."""
    article = written(("옛 문장", "3m"))
    Commentary.objects.update(predictions=[prediction("2099년까지 내린다", "1m")])

    status, _ = save(admin_client, article, hidden="on")

    assert status == 302
    assert Commentary.objects.get().hidden


def test_saving_article_without_touching_legacy_commentary(admin_client: Client) -> None:
    article = written(("옛 문장", "3m"))
    Commentary.objects.update(predictions=[prediction("2099년까지 내린다", "1m")])

    status, _ = save(admin_client, article)

    assert status == 302
    assert Prediction.objects.get().claim == "옛 문장"  # 손대지 않은 분석은 저장도 동기화도 없다
