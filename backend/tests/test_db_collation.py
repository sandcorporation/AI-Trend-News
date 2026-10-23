"""The MySQL database must compare like Postgres did: case- and accent-sensitive (ADR 0004).

The server default utf8mb4_0900_ai_ci folds case and accents, which would let /a/<SLUG> serve the
article, make get_or_create return a differently-cased tag, and break a prediction upsert whose
claim only differs in case. The database is created with utf8mb4_0900_as_cs; these pin that.
"""

from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test import Client

from articles.models import Prediction, Tag
from services.predictions import judge_prediction
from tests.test_predictions import featured_article_id, write
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db


def test_database_uses_case_and_accent_sensitive_utf8mb4() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@character_set_database, @@collation_database")
        assert cursor.fetchone() == ("utf8mb4", "utf8mb4_0900_as_cs")


def test_uppercased_article_slug_is_not_found() -> None:
    article = make_article("대소문자")

    assert Client().get(f"/a/{article.slug.upper()}").status_code == 404


def test_tag_lookup_keeps_case_and_accents_apart() -> None:
    make_article("태그", tags=("OpenAI", "café"))

    _, created = Tag.objects.get_or_create(name="openai")
    assert created
    assert not Tag.objects.filter(name="cafe").exists()
    assert sorted(Tag.objects.values_list("name", flat=True)) == ["OpenAI", "café", "openai"]


def test_prediction_claim_differing_only_in_case_is_a_new_row() -> None:
    article_id = featured_article_id()
    write(article_id, ("GPT-6 출시", "3m"))
    judge_prediction(Prediction.objects.get().pk, "hit", now=datetime.now(UTC))

    write(article_id, ("gpt-6 출시", "3m"))

    assert sorted(Prediction.objects.values_list("claim", "verdict")) == [
        ("GPT-6 출시", "hit"),
        ("gpt-6 출시", "pending"),
    ]
