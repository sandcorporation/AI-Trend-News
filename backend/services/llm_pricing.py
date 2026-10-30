"""모델별 단가표와 추정 비용(설계 §6). 값은 **추정**이다 — Console 사용량과 맞춰 고친다.

단가는 백만 토큰당 US$(2026-10-30 공식 가격). 캐시 쓰기는 입력 단가 × 1.25, 배치는 × 0.5.
Haiku 5.5는 프롬프트(입력+캐시 쓰기+캐시 읽기)가 100K 토큰을 넘으면 그 호출 전체가 긴 문맥 단가다.
표에 없는 모델은 `UnknownModel` — 비용이 조용히 0으로 남지 않게 실행을 시작하지 않는다.
"""

from dataclasses import dataclass
from decimal import Decimal

from anthropic.types import Usage

MILLION = Decimal(1_000_000)
CACHE_WRITE_FACTOR = Decimal("1.25")
BATCH_FACTOR = Decimal("0.5")
LONG_CONTEXT_THRESHOLD = 100_000
COST_QUANTUM = Decimal("0.000001")  # EditorCall.cost_usd Decimal(10,6)


@dataclass(frozen=True)
class Rate:
    input: Decimal
    output: Decimal
    cache_read: Decimal


@dataclass(frozen=True)
class Price:
    base: Rate
    long_context: Rate | None = None  # 프롬프트가 LONG_CONTEXT_THRESHOLD를 넘을 때


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(Rate(Decimal("2"), Decimal("10"), Decimal("0.20"))),
    "claude-opus-5-5": Price(Rate(Decimal("4"), Decimal("20"), Decimal("0.20"))),
    "claude-haiku-5-5": Price(
        Rate(Decimal("0.10"), Decimal("0.50"), Decimal("0.01")),
        # 긴 문맥 캐시 읽기 단가는 공식 표에 따로 없어 기본과 같은 비율(입력의 10%)로 둔다.
        long_context=Rate(Decimal("0.50"), Decimal("2.50"), Decimal("0.05")),
    ),
}


class UnknownModel(Exception):
    """가격표에 없는 모델."""


def check_model(model: str) -> None:
    if model not in PRICES:
        raise UnknownModel(f"가격표(services/llm_pricing.py)에 없는 모델: {model}")


def cost_usd(model: str, usage: Usage, *, batch: bool = False) -> Decimal:
    check_model(model)
    price = PRICES[model]
    creation = usage.cache_creation_input_tokens or 0
    read = usage.cache_read_input_tokens or 0
    prompt = usage.input_tokens + creation + read
    rate = price.base
    if price.long_context is not None and prompt > LONG_CONTEXT_THRESHOLD:
        rate = price.long_context
    total = (
        usage.input_tokens * rate.input
        + creation * rate.input * CACHE_WRITE_FACTOR
        + read * rate.cache_read
        + usage.output_tokens * rate.output
    ) / MILLION
    if batch:
        total *= BATCH_FACTOR
    return total.quantize(COST_QUANTUM)
