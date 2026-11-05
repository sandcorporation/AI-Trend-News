"""운영 설정(#164): admin에서 바꾸고 재시작 없이 web·scheduler 모두에 반영하는 설정.

읽기 `<설정>.get()`: DB 값이 있으면 DB, 없으면 지금의 `settings.<이름>`(.env·시험 덮어쓰기).
등록부(`REGISTRY`) 밖의 이름은 저장하지 못한다.

캐시: 프로세스마다 DB 값 전체를 버전 번호와 함께 메모리에 둔다. 읽을 때 DB의 버전 번호(PK 한 줄)를
확인하되 `RUNTIME_SETTINGS_CHECK_SECONDS`(기본 1초, 0이면 매번) 간격으로만 본다. 버전이 바뀌었으면
전체를 다시 읽는다. 저장·지우기는 트랜잭션 안에서 버전을 `F()+1`로 올리고 자기 프로세스 캐시를
비운다. 이 저장소에서 프로세스(web 워커·scheduler)가 함께 보는 것은 MySQL뿐이라(CACHES는 LocMem)
공유 무효화 신호를 DB 버전 행 하나에 둔다 — 다른 프로세스는 늦어도 확인 간격 뒤에 새 값을 읽는다.

비밀(`secret=True`)은 `RUNTIME_SETTINGS_KEY`(Fernet 키)로 암호화해 저장한다. 키가 없으면 비밀
저장을 거절하고(읽기는 .env로 계속), 복호화가 실패하면 그 값은 없는 것으로 보고 .env로 돌아간다.
값은 로그·예외 문구·이력 어디에도 남기지 않는다(로그에는 이름만).
"""

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F

from core.models import RuntimeSetting, RuntimeSettingChange, RuntimeSettingsVersion

logger = logging.getLogger(__name__)

Source = Literal["db", "env", "default"]
SOURCE_LABELS: dict[Source, str] = {"db": "DB", "env": ".env", "default": "기본값"}


class RuntimeSettingError(Exception):
    """저장 거절. 문구에 값을 넣지 않는다."""


class UnknownSetting(RuntimeSettingError):
    pass


class InvalidSettingValue(RuntimeSettingError):
    pass


class EncryptionKeyMissing(RuntimeSettingError):
    pass


# --- 값 해석기: 저장 문자열 → 값. 틀리면 ValueError(문구에 값을 넣지 않는다) ---


def _choice(options: Callable[[], tuple[str, ...]]) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        value = raw.strip()
        if value not in options():
            raise ValueError(f"가능한 값: {', '.join(o or '(빈 값)' for o in options())}")
        return value

    return parse


def _integer(low: int, high: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(raw.strip())
        except ValueError:
            raise ValueError("정수가 아니다") from None
        if not low <= value <= high:
            raise ValueError(f"{low}~{high} 사이여야 한다")
        return value

    return parse


def _number(low: float, high: float) -> Callable[[str], float]:
    def parse(raw: str) -> float:
        try:
            value = float(raw.strip())
        except ValueError:
            raise ValueError("숫자가 아니다") from None
        if not low <= value <= high:
            raise ValueError(f"{low:g}~{high:g} 사이여야 한다")
        return value

    return parse


def _decimal(low: Decimal, high: Decimal) -> Callable[[str], Decimal]:
    def parse(raw: str) -> Decimal:
        try:
            value = Decimal(raw.strip())
        except InvalidOperation:
            raise ValueError("숫자가 아니다") from None
        if not value.is_finite() or not low <= value <= high:
            raise ValueError(f"{low}~{high} 사이여야 한다")
        return value

    return parse


TRUE_WORDS = ("true", "1", "yes", "on")
FALSE_WORDS = ("false", "0", "no", "off")


def _flag(raw: str) -> bool:
    value = raw.strip().lower()
    if value in TRUE_WORDS:
        return True
    if value in FALSE_WORDS:
        return False
    raise ValueError("true 또는 false")


def _hours(raw: str) -> tuple[int, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    try:
        hours = tuple(int(p) for p in parts)
    except ValueError:
        raise ValueError("쉼표로 나눈 시(0~23)") from None
    if not hours or any(not 0 <= h <= 23 for h in hours) or len(set(hours)) != len(hours):
        raise ValueError("쉼표로 나눈 서로 다른 시(0~23), 하나 이상")
    return hours


def _fallback_template(raw: str) -> str:
    value = raw.strip()
    if value and not (re.match(r"^https?://", value) and "{url}" in value):
        raise ValueError("비우거나 {url}을 담은 http(s) 주소")
    return value


def _secret(raw: str) -> str:
    value = raw.strip()
    if not value or any(ch.isspace() for ch in value) or len(value) > 500:
        raise ValueError("공백 없는 값(500자 이하)")
    return value


def _models() -> tuple[str, ...]:
    from services.llm_pricing import PRICES

    return tuple(PRICES)


def _models_or_empty() -> tuple[str, ...]:
    return ("", *_models())


def _efforts() -> tuple[str, ...]:
    from services.editor_prompts import EFFORTS

    return EFFORTS


def _show_hours(value: tuple[int, ...]) -> str:
    return ",".join(str(h) for h in value)


def _show_float(value: float) -> str:
    return f"{value:g}"


def _show_flag(value: bool) -> str:
    return "true" if value else "false"


# --- 등록부 ---


@dataclass(frozen=True)
class Setting[T]:
    name: str
    group: str
    help: str
    parse: Callable[[str], T]
    show: Callable[[T], str] = str
    secret: bool = False
    choices: Callable[[], tuple[str, ...]] | None = None

    def get(self) -> T:
        """DB 값이 있으면 그것, 없으면(또는 해석 실패면) `settings.<이름>`."""
        stored = _cache.values().get(self.name)
        if stored is not None:
            try:
                return self.parse(stored)
            except ValueError:
                logger.warning("운영 설정 %s: DB 값을 해석하지 못해 .env 값을 쓴다", self.name)
        return self.fallback()

    def fallback(self) -> T:
        value: T = getattr(settings, self.name)
        return value

    def source(self) -> Source:
        if self.name in _cache.values():
            return "db"
        return "env" if self.name in os.environ else "default"

    def current_text(self) -> str:
        """일반 설정의 지금 값(화면용). 비밀에는 쓰지 않는다."""
        if self.secret:
            raise RuntimeSettingError("비밀 값은 보이지 않는다")
        return self.show(self.get())


SANDCORP_NEWS_CLAUDE_KEY = Setting(
    "SANDCORP_NEWS_CLAUDE_KEY",
    "AI 키",
    "Anthropic API 키(수집 AI 판별·편집 파이프라인). 쓰기 전용.",
    _secret,
    secret=True,
)

COLLECT_AI_FILTER_MODE = Setting(
    "COLLECT_AI_FILTER_MODE",
    "수집",
    "수집 AI 판별: off / shadow(기록만) / on(밖이면 거름).",
    _choice(lambda: ("off", "shadow", "on")),
    choices=lambda: ("off", "shadow", "on"),
)
COLLECT_AI_FILTER_MODEL = Setting(
    "COLLECT_AI_FILTER_MODEL",
    "수집",
    "수집 AI 판별 모델(가격표에 있는 모델만).",
    _choice(_models),
    choices=_models,
)
COLLECT_AI_FILTER_BATCH = Setting(
    "COLLECT_AI_FILTER_BATCH", "수집", "판별 요청 하나에 넣는 수집물 수.", _integer(1, 200)
)
COLLECT_AI_FILTER_TIMEOUT = Setting(
    "COLLECT_AI_FILTER_TIMEOUT", "수집", "판별 요청 시간 한도(초).", _number(1, 600), _show_float
)
COLLECT_MAX_AGE_DAYS = Setting(
    "COLLECT_MAX_AGE_DAYS",
    "수집",
    "발행이 이보다 오래된 글은 수집하지 않는다(일).",
    _integer(1, 36500),
)
PENDING_MAX_AGE_HOURS = Setting(
    "PENDING_MAX_AGE_HOURS", "수집", "미처리 수집물을 버림으로 바꾸는 시간(시간).", _integer(1, 720)
)
FETCH_FALLBACK_URL = Setting(
    "FETCH_FALLBACK_URL",
    "수집",
    "직접 GET이 막혔을 때 한 번 더 받는 주소 템플릿({url}). 비우면 끔.",
    _fallback_template,
)

EDITOR_PIPELINE_MODE = Setting(
    "EDITOR_PIPELINE_MODE",
    "편집",
    "편집 파이프라인: off / shadow(기록만) / live(저장).",
    _choice(lambda: ("off", "shadow", "live")),
    choices=lambda: ("off", "shadow", "live"),
)
EDITOR_RUN_HOURS = Setting(
    "EDITOR_RUN_HOURS", "편집", "편집 시각 슬롯(KST 시, 쉼표로).", _hours, _show_hours
)
EDITOR_MODEL_CLASSIFY = Setting(
    "EDITOR_MODEL_CLASSIFY", "편집", "1단계 분류 모델.", _choice(_models), choices=_models
)
EDITOR_EFFORT_CLASSIFY = Setting(
    "EDITOR_EFFORT_CLASSIFY", "편집", "1단계 분류 effort.", _choice(_efforts), choices=_efforts
)
EDITOR_MODEL_WRITE = Setting(
    "EDITOR_MODEL_WRITE", "편집", "2단계 작성 모델.", _choice(_models), choices=_models
)
EDITOR_EFFORT_WRITE = Setting(
    "EDITOR_EFFORT_WRITE", "편집", "2단계 작성 effort.", _choice(_efforts), choices=_efforts
)
EDITOR_MODEL_COMMENTARY = Setting(
    "EDITOR_MODEL_COMMENTARY", "편집", "3단계 분석·포스트 모델.", _choice(_models), choices=_models
)
EDITOR_EFFORT_COMMENTARY = Setting(
    "EDITOR_EFFORT_COMMENTARY",
    "편집",
    "3단계 분석·포스트 effort.",
    _choice(_efforts),
    choices=_efforts,
)
EDITOR_MODEL_CLASSIFY_COMPARE = Setting(
    "EDITOR_MODEL_CLASSIFY_COMPARE",
    "편집",
    "그림자 비교용 두 번째 분류 모델. 빈 값이면 끔.",
    _choice(_models_or_empty),
    choices=_models_or_empty,
)
EDITOR_MONTHLY_BUDGET_USD = Setting(
    "EDITOR_MONTHLY_BUDGET_USD",
    "편집",
    "편집 월 예산(US$). 넘으면 단계를 시작하지 않는다.",
    _decimal(Decimal(0), Decimal(1000)),
)
EDITOR_USE_BATCH = Setting(
    "EDITOR_USE_BATCH",
    "편집",
    "작성·분석을 배치 API로(false면 동기).",
    _flag,
    _show_flag,
    choices=lambda: ("true", "false"),
)
EDITOR_BATCH_WAIT_SECONDS = Setting(
    "EDITOR_BATCH_WAIT_SECONDS", "편집", "주기마다 배치를 기다리는 시간(초).", _integer(0, 3000)
)
EDITOR_MAX_ITEMS = Setting(
    "EDITOR_MAX_ITEMS", "편집", "분류 입력 미처리 수집물 상한.", _integer(1, 500)
)
EDITOR_MAX_WRITES = Setting("EDITOR_MAX_WRITES", "편집", "실행당 작성 호출 상한.", _integer(0, 200))
EDITOR_MAX_FEATURED = Setting("EDITOR_MAX_FEATURED", "편집", "실행당 추천 상한.", _integer(0, 50))
EDITOR_MAX_COMMENTARY = Setting(
    "EDITOR_MAX_COMMENTARY", "편집", "슬롯당 분석·포스트 상한(0이면 3단계 끔).", _integer(0, 50)
)

type AnySetting = (
    Setting[str]
    | Setting[int]
    | Setting[float]
    | Setting[Decimal]
    | Setting[bool]
    | Setting[tuple[int, ...]]
)

REGISTRY: dict[str, AnySetting] = {
    s.name: s
    for s in (
        SANDCORP_NEWS_CLAUDE_KEY,
        COLLECT_AI_FILTER_MODE,
        COLLECT_AI_FILTER_MODEL,
        COLLECT_AI_FILTER_BATCH,
        COLLECT_AI_FILTER_TIMEOUT,
        COLLECT_MAX_AGE_DAYS,
        PENDING_MAX_AGE_HOURS,
        FETCH_FALLBACK_URL,
        EDITOR_PIPELINE_MODE,
        EDITOR_RUN_HOURS,
        EDITOR_MODEL_CLASSIFY,
        EDITOR_EFFORT_CLASSIFY,
        EDITOR_MODEL_WRITE,
        EDITOR_EFFORT_WRITE,
        EDITOR_MODEL_COMMENTARY,
        EDITOR_EFFORT_COMMENTARY,
        EDITOR_MODEL_CLASSIFY_COMPARE,
        EDITOR_MONTHLY_BUDGET_USD,
        EDITOR_USE_BATCH,
        EDITOR_BATCH_WAIT_SECONDS,
        EDITOR_MAX_ITEMS,
        EDITOR_MAX_WRITES,
        EDITOR_MAX_FEATURED,
        EDITOR_MAX_COMMENTARY,
    )
}


# --- 암호화 ---


def _fernet() -> Fernet | None:
    key = getattr(settings, "RUNTIME_SETTINGS_KEY", "")
    if not key:
        return None
    try:
        return Fernet(key)
    except ValueError:
        logger.error("RUNTIME_SETTINGS_KEY가 Fernet 키 형식이 아니다(비밀 설정은 .env 값을 쓴다)")
        return None


def encryption_ready() -> bool:
    return _fernet() is not None


# --- 캐시 ---


def _db_version() -> int:
    version = RuntimeSettingsVersion.objects.filter(pk=1).values_list("version", flat=True).first()
    return version or 0


def _load() -> dict[str, str]:
    """DB 값 전체(비밀은 복호화). 복호화·등록부 밖 이름은 건너뛴다."""
    fernet = _fernet()
    values: dict[str, str] = {}
    for name, stored in RuntimeSetting.objects.values_list("name", "value"):
        spec = REGISTRY.get(name)
        if spec is None:
            continue
        if not spec.secret:
            values[name] = stored
            continue
        if fernet is None:
            logger.warning("운영 설정 %s: 암호화 키가 없어 .env 값을 쓴다", name)
            continue
        try:
            values[name] = fernet.decrypt(stored.encode()).decode()
        except InvalidToken:
            logger.warning("운영 설정 %s: 복호화 실패(키가 바뀌었나) — .env 값을 쓴다", name)
    return values


@dataclass
class SettingsCache:
    """프로세스 하나의 캐시. `clock`은 시험이 바꾼다(단조 초)."""

    clock: Callable[[], float] = time.monotonic
    _values: dict[str, str] | None = None
    _version: int = -1
    _checked_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def values(self) -> dict[str, str]:
        now = self.clock()
        interval = float(getattr(settings, "RUNTIME_SETTINGS_CHECK_SECONDS", 1.0))
        with self._lock:
            if self._values is not None and now - self._checked_at < interval:
                return self._values
            try:
                version = _db_version()
                if self._values is None or version != self._version:
                    self._values, self._version = _load(), version
            except DatabaseError:
                logger.warning("운영 설정을 DB에서 읽지 못했다 — .env 값을 쓴다", exc_info=True)
                if self._values is None:
                    return {}
            self._checked_at = now
            return self._values

    def clear(self) -> None:
        with self._lock:
            self._values, self._version = None, -1


class StaticCache(SettingsCache):
    """DB를 쓰지 않는 시험(django_db 없음)용: 늘 비어 있다(= 모두 settings 값)."""

    def values(self) -> dict[str, str]:
        return {}


_cache: SettingsCache = SettingsCache()


def set_cache(cache: SettingsCache) -> SettingsCache:
    """등록 지점. 이전 캐시를 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _cache
    previous, _cache = _cache, cache
    return previous


def clear_cache() -> None:
    _cache.clear()


# --- 저장·지우기 ---


def _spec(name: str) -> AnySetting:
    spec = REGISTRY.get(name)
    if spec is None:
        raise UnknownSetting(f"등록부에 없는 설정: {name}")
    return spec


def _bump() -> None:
    if not RuntimeSettingsVersion.objects.filter(pk=1).update(version=F("version") + 1):
        RuntimeSettingsVersion.objects.create(pk=1, version=1)
    _cache.clear()
    transaction.on_commit(_cache.clear)


def save(name: str, raw: str, *, actor: str) -> None:
    """검증해 저장한다(비밀은 암호화). 버전을 올리고 이력에 값 없이 남긴다."""
    spec = _spec(name)
    try:
        value = spec.parse(raw)
    except ValueError as error:
        raise InvalidSettingValue(f"{name}: {error}") from None
    if spec.secret:
        fernet = _fernet()
        if fernet is None:
            raise EncryptionKeyMissing(
                "암호화 키 없음 — 대장이 .env에 RUNTIME_SETTINGS_KEY를 넣어야 함"
            )
        stored = fernet.encrypt(str(value).encode()).decode()
    else:
        stored = spec.show(value)  # type: ignore[arg-type]  # spec과 value는 같은 T
    with transaction.atomic():
        RuntimeSetting.objects.update_or_create(
            name=name, defaults={"value": stored, "updated_by": actor}
        )
        RuntimeSettingChange.objects.create(
            name=name, action=RuntimeSettingChange.Action.SET, changed_by=actor
        )
        _bump()
    logger.info("운영 설정 %s 저장(%s)", name, actor)


def clear(name: str, *, actor: str) -> bool:
    """DB 값을 지운다(→ .env로 돌아감). 지운 것이 없으면 False."""
    _spec(name)
    with transaction.atomic():
        deleted, _ = RuntimeSetting.objects.filter(name=name).delete()
        if not deleted:
            return False
        RuntimeSettingChange.objects.create(
            name=name, action=RuntimeSettingChange.Action.CLEAR, changed_by=actor
        )
        _bump()
    logger.info("운영 설정 %s 지움(%s)", name, actor)
    return True


# --- 화면용 ---


@dataclass(frozen=True)
class Row:
    """admin 한 줄. 비밀 값은 담지 않는다(끝 4자리만)."""

    name: str
    group: str
    help: str
    secret: bool
    source: str
    current: str  # 일반 설정의 지금 값(비밀은 "")
    choices: tuple[str, ...] | None
    is_set: bool  # 비밀: 지금 값(DB 또는 .env)이 있는가
    last4: str  # 비밀: 끝 4자리(값이 8자 미만이면 빈 값)
    in_db: bool
    updated_at: object
    updated_by: str


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 8 else ""


def rows() -> list[Row]:
    stored = {s.name: s for s in RuntimeSetting.objects.all()}
    result: list[Row] = []
    for spec in REGISTRY.values():
        db_row = stored.get(spec.name)
        source = SOURCE_LABELS[spec.source()]
        if spec.secret:
            value = str(spec.get())
            current, is_set, last4 = "", bool(value), _last4(value)
        else:
            current, is_set, last4 = spec.current_text(), True, ""
        result.append(
            Row(
                name=spec.name,
                group=spec.group,
                help=spec.help,
                secret=spec.secret,
                source=source,
                current=current,
                choices=spec.choices() if spec.choices else None,
                is_set=is_set,
                last4=last4,
                in_db=db_row is not None,
                updated_at=db_row.updated_at if db_row else None,
                updated_by=db_row.updated_by if db_row else "",
            )
        )
    return result
