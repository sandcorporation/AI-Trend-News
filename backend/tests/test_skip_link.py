"""본문 건너뛰기(#203): 첫 초점 요소는 건너뛰기 링크, 목표는 `<main id="main">`."""

import re

import pytest
from django.test import Client
from django.utils import timezone
from sandcorp_authkit.testing import FixedClock

from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

FOCUSABLE = re.compile(r"<(?:a|button|input|select|textarea)\b[^>]*>")


def test_skip_link_is_first_focusable_on_every_page(
    client: Client, frozen_clock: FixedClock
) -> None:
    frozen_clock.moment = timezone.now()
    article = make_article("기사")
    day = f"/news/{timezone.localdate(article.published_at).isoformat()}"

    for url in ["/", f"/a/{article.slug}", day, "/predictions", "/contact"]:
        response = client.get(url)
        assert response.status_code == 200, url
        body = response.content.decode().split('<div id="root">', 1)[1]
        first = FOCUSABLE.search(body)
        assert first and first.group(0) == '<a class="skip-link" href="#main">', url
        assert body.count('<main id="main" class="main" tabindex="-1">') == 1, url
