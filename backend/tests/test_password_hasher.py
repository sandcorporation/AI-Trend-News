"""시험의 싼 비밀번호 해시가 운영 설정으로 새지 않는지 고정한다(#102)."""

from django.conf import global_settings, settings

from config import settings as production_settings


def test_tests_use_cheap_hasher_but_production_keeps_django_default() -> None:
    assert settings.PASSWORD_HASHERS[0].endswith("MD5PasswordHasher")
    assert (
        getattr(production_settings, "PASSWORD_HASHERS", global_settings.PASSWORD_HASHERS)
        == global_settings.PASSWORD_HASHERS
    )
