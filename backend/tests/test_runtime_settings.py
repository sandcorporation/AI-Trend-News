"""운영 설정(#164): 우선순위·프로세스 간 반영·암호화·admin 쓰기 전용·비밀 비노출."""

import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import Protocol

import pytest
from cryptography.fernet import Fernet
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import Client

from adapters import anthropic as anthropic_adapter
from adapters.anthropic import KeyCheck, get_llm, set_key_checker, set_llm
from core.models import RuntimeSetting, RuntimeSettingChange, RuntimeSettingsVersion
from services import runtime_settings as rs

pytestmark = pytest.mark.django_db

FAKE_KEY = "sk-ant-fake-0123456789-ZQXW"  # 가짜 키. 끝 4자리 ZQXW
ADMIN_URL = f"/{django_settings.ADMIN_PATH}core/runtimesetting/"


class SettingsWrapper(Protocol):
    COLLECT_AI_FILTER_MODE: str
    SANDCORP_NEWS_CLAUDE_KEY: str
    RUNTIME_SETTINGS_KEY: str
    RUNTIME_SETTINGS_CHECK_SECONDS: float
    EDITOR_MONTHLY_BUDGET_USD: Decimal
    LLM_FORBID_REAL: bool


@pytest.fixture
def fernet_key(settings: SettingsWrapper) -> str:
    key = Fernet.generate_key().decode()
    settings.RUNTIME_SETTINGS_KEY = key
    return key


class Ticker:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def owner_client(client: Client) -> Client:
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)
    return client


# --- 우선순위 ---


def test_db_overrides_settings_and_clear_falls_back(settings: SettingsWrapper) -> None:
    settings.COLLECT_AI_FILTER_MODE = "on"
    assert rs.COLLECT_AI_FILTER_MODE.get() == "on"
    assert rs.COLLECT_AI_FILTER_MODE.source() != "db"

    rs.save("COLLECT_AI_FILTER_MODE", "off", actor="owner")
    assert rs.COLLECT_AI_FILTER_MODE.get() == "off"
    assert rs.COLLECT_AI_FILTER_MODE.source() == "db"

    assert rs.clear("COLLECT_AI_FILTER_MODE", actor="owner") is True
    assert rs.COLLECT_AI_FILTER_MODE.get() == "on"
    assert rs.clear("COLLECT_AI_FILTER_MODE", actor="owner") is False


def test_typed_values_round_trip() -> None:
    rs.save("EDITOR_RUN_HOURS", " 7, 13 ", actor="o")
    rs.save("EDITOR_USE_BATCH", "false", actor="o")
    rs.save("EDITOR_MONTHLY_BUDGET_USD", "12.5", actor="o")
    rs.save("COLLECT_AI_FILTER_TIMEOUT", "20", actor="o")
    assert rs.EDITOR_RUN_HOURS.get() == (7, 13)
    assert rs.EDITOR_USE_BATCH.get() is False
    assert rs.EDITOR_MONTHLY_BUDGET_USD.get() == Decimal("12.5")
    assert rs.COLLECT_AI_FILTER_TIMEOUT.get() == 20.0


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("COLLECT_AI_FILTER_MODE", "maybe"),
        ("COLLECT_AI_FILTER_MODEL", "gpt-9"),
        ("EDITOR_MAX_ITEMS", "-3"),
        ("EDITOR_RUN_HOURS", "25"),
        ("FETCH_FALLBACK_URL", "https://proxy.example.com/"),
        ("EDITOR_USE_BATCH", "perhaps"),
    ],
)
def test_invalid_values_are_rejected(name: str, raw: str) -> None:
    with pytest.raises(rs.InvalidSettingValue) as error:
        rs.save(name, raw, actor="o")
    assert raw not in str(error.value)
    assert not RuntimeSetting.objects.exists()


def test_unknown_name_is_rejected() -> None:
    with pytest.raises(rs.UnknownSetting):
        rs.save("DJANGO_SECRET_KEY", "x", actor="o")
    with pytest.raises(rs.UnknownSetting):
        rs.clear("DB_PASSWORD", actor="o")


def test_excluded_settings_are_not_registered() -> None:
    for name in (
        "DJANGO_SECRET_KEY",
        "DB_PASSWORD",
        "AUTH_CLIENT_SECRET",
        "SITE_URL",
        "SCHEDULER_INTERVAL",
        "RUNTIME_SETTINGS_KEY",
        "DISCORD_WEBHOOK_URL",
    ):
        assert name not in rs.REGISTRY


# --- 캐시와 프로세스 간 반영 ---


def test_other_process_sees_new_value_after_check_interval(settings: SettingsWrapper) -> None:
    settings.RUNTIME_SETTINGS_CHECK_SECONDS = 1.0
    settings.COLLECT_AI_FILTER_MODE = "on"
    ticker = Ticker()
    other = rs.SettingsCache(clock=ticker)  # 다른 프로세스의 캐시(메모리를 나누지 않는다)
    assert "COLLECT_AI_FILTER_MODE" not in other.values()

    rs.save("COLLECT_AI_FILTER_MODE", "shadow", actor="owner")
    # 저장한 프로세스는 바로 새 값
    assert rs.COLLECT_AI_FILTER_MODE.get() == "shadow"
    # 다른 프로세스: 확인 간격 안에서는 옛 값, 간격 뒤에는 새 값
    ticker.now += 0.5
    assert "COLLECT_AI_FILTER_MODE" not in other.values()
    ticker.now += 0.6
    assert other.values()["COLLECT_AI_FILTER_MODE"] == "shadow"

    rs.clear("COLLECT_AI_FILTER_MODE", actor="owner")
    ticker.now += 1.1
    assert "COLLECT_AI_FILTER_MODE" not in other.values()


def test_cache_does_not_query_within_interval(settings: SettingsWrapper) -> None:
    settings.RUNTIME_SETTINGS_CHECK_SECONDS = 1.0
    ticker = Ticker()
    cache = rs.SettingsCache(clock=ticker)
    cache.values()  # 버전 + 전체 읽기
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        for _ in range(5):
            cache.values()
    assert len(queries) == 0
    ticker.now += 2
    with CaptureQueriesContext(connection) as queries:
        cache.values()
    assert len(queries) == 1  # 버전만(바뀌지 않음)


def test_save_bumps_version() -> None:
    before = RuntimeSettingsVersion.objects.get(pk=1).version
    rs.save("EDITOR_MAX_ITEMS", "10", actor="o")
    rs.clear("EDITOR_MAX_ITEMS", actor="o")
    assert RuntimeSettingsVersion.objects.get(pk=1).version == before + 2


# --- 암호화 ---


def test_secret_is_encrypted_at_rest(fernet_key: str) -> None:
    rs.save("SANDCORP_NEWS_CLAUDE_KEY", FAKE_KEY, actor="owner")
    stored = RuntimeSetting.objects.get(name="SANDCORP_NEWS_CLAUDE_KEY").value
    assert FAKE_KEY not in stored
    assert "ZQXW" not in stored
    assert Fernet(fernet_key).decrypt(stored.encode()).decode() == FAKE_KEY
    assert rs.SANDCORP_NEWS_CLAUDE_KEY.get() == FAKE_KEY
    assert FAKE_KEY not in repr(RuntimeSetting.objects.get()) + str(RuntimeSetting.objects.get())


def test_secret_save_refused_without_key(settings: SettingsWrapper) -> None:
    settings.RUNTIME_SETTINGS_KEY = ""
    with pytest.raises(rs.EncryptionKeyMissing):
        rs.save("SANDCORP_NEWS_CLAUDE_KEY", FAKE_KEY, actor="owner")
    assert not RuntimeSetting.objects.exists()
    rs.save("EDITOR_MAX_ITEMS", "12", actor="owner")  # 일반 설정은 키 없이도
    assert rs.EDITOR_MAX_ITEMS.get() == 12


def test_wrong_key_falls_back_to_env(
    settings: SettingsWrapper, fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    settings.SANDCORP_NEWS_CLAUDE_KEY = "env-key"
    rs.save("SANDCORP_NEWS_CLAUDE_KEY", FAKE_KEY, actor="owner")
    settings.RUNTIME_SETTINGS_KEY = Fernet.generate_key().decode()  # 키가 바뀜
    rs.clear_cache()
    with caplog.at_level(logging.DEBUG):
        assert rs.SANDCORP_NEWS_CLAUDE_KEY.get() == "env-key"
    assert "SANDCORP_NEWS_CLAUDE_KEY" in caplog.text
    assert FAKE_KEY not in caplog.text


def test_get_llm_uses_db_key(
    settings: SettingsWrapper, fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.SANDCORP_NEWS_CLAUDE_KEY = "env-key"
    settings.LLM_FORBID_REAL = False
    seen: list[str] = []

    class Recorder:
        def __init__(self, api_key: str, timeout: float = 600.0, max_retries: int = 2) -> None:
            seen.append(api_key)

    monkeypatch.setattr(anthropic_adapter, "AnthropicClient", Recorder)
    previous = set_llm(None)
    try:
        get_llm()
        rs.save("SANDCORP_NEWS_CLAUDE_KEY", FAKE_KEY, actor="owner")
        get_llm()
        rs.clear("SANDCORP_NEWS_CLAUDE_KEY", actor="owner")
        get_llm()
    finally:
        set_llm(previous)
    assert seen == ["env-key", FAKE_KEY, "env-key"]


# --- admin ---


@pytest.fixture
def key_checks() -> Iterator[list[str]]:
    checked: list[str] = []

    def fake(key: str) -> KeyCheck:
        checked.append(key)
        return KeyCheck(True, "연결 성공(200)")

    previous = set_key_checker(fake)
    yield checked
    set_key_checker(previous)


def test_admin_secret_is_write_only(
    owner_client: Client, fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = owner_client.post(
            ADMIN_URL,
            {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "save", "value": FAKE_KEY},
            follow=True,
        )
    html = response.content.decode()
    assert response.status_code == 200
    assert FAKE_KEY not in html
    assert "설정됨" in html and "ZQXW" in html
    assert FAKE_KEY not in caplog.text
    change = RuntimeSettingChange.objects.get()
    assert (change.name, change.action, change.changed_by) == (
        "SANDCORP_NEWS_CLAUDE_KEY",
        "set",
        "owner",
    )
    assert FAKE_KEY not in str(list(RuntimeSettingChange.objects.values()))

    # 빈 입력은 기존 값 유지
    owner_client.post(ADMIN_URL, {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "save", "value": ""})
    assert rs.SANDCORP_NEWS_CLAUDE_KEY.get() == FAKE_KEY
    assert RuntimeSettingChange.objects.count() == 1

    # 지우기 버튼
    owner_client.post(ADMIN_URL, {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "clear"})
    assert not RuntimeSetting.objects.exists()
    assert rs.SANDCORP_NEWS_CLAUDE_KEY.get() == ""


def test_admin_shows_missing_encryption_key(owner_client: Client) -> None:
    html = owner_client.get(ADMIN_URL).content.decode()
    assert "RUNTIME_SETTINGS_KEY" in html
    response = owner_client.post(
        ADMIN_URL,
        {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "save", "value": FAKE_KEY},
        follow=True,
    )
    assert "암호화 키 없음" in response.content.decode()
    assert not RuntimeSetting.objects.exists()


def test_admin_general_setting_save_and_clear(
    owner_client: Client, settings: SettingsWrapper
) -> None:
    settings.COLLECT_AI_FILTER_MODE = "on"
    owner_client.post(ADMIN_URL, {"name": "COLLECT_AI_FILTER_MODE", "op": "save", "value": "off"})
    assert rs.COLLECT_AI_FILTER_MODE.get() == "off"
    html = owner_client.get(ADMIN_URL).content.decode()
    assert "DB 값 지우기" in html
    owner_client.post(ADMIN_URL, {"name": "COLLECT_AI_FILTER_MODE", "op": "clear"})
    assert rs.COLLECT_AI_FILTER_MODE.get() == "on"
    assert list(RuntimeSettingChange.objects.values_list("action", flat=True)) == ["clear", "set"]


def test_admin_connection_test_uses_typed_or_current_key(
    owner_client: Client, fernet_key: str, key_checks: list[str]
) -> None:
    rs.save("SANDCORP_NEWS_CLAUDE_KEY", FAKE_KEY, actor="owner")
    response = owner_client.post(
        ADMIN_URL, {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "test", "value": ""}, follow=True
    )
    owner_client.post(
        ADMIN_URL, {"name": "SANDCORP_NEWS_CLAUDE_KEY", "op": "test", "value": "sk-ant-typed"}
    )
    assert key_checks == [FAKE_KEY, "sk-ant-typed"]
    html = response.content.decode()
    assert "연결 성공(200)" in html
    assert FAKE_KEY not in html
    assert rs.SANDCORP_NEWS_CLAUDE_KEY.get() == FAKE_KEY  # 시험은 저장하지 않는다


def test_real_key_check_is_blocked_in_tests() -> None:
    result = anthropic_adapter._real_key_check(FAKE_KEY)
    assert result.ok is False
    assert FAKE_KEY not in result.message


def test_admin_rejects_non_owner(client: Client) -> None:
    staff = get_user_model().objects.create_user("staff", password=None, is_staff=True)
    client.force_login(staff)
    assert client.get(ADMIN_URL).status_code == 403
    response = client.post(
        ADMIN_URL, {"name": "COLLECT_AI_FILTER_MODE", "op": "save", "value": "off"}
    )
    assert response.status_code == 403
    assert not RuntimeSetting.objects.exists()


def test_admin_lists_registry_with_sources(owner_client: Client) -> None:
    html = owner_client.get(ADMIN_URL).content.decode()
    for name in rs.REGISTRY:
        assert f'data-setting="{name}"' in html
