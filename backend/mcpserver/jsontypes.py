"""JSON 값의 타입(portfolio `documents.validation.JSON`과 같은 정의)."""

type JSON = dict[str, JSON] | list[JSON] | str | int | float | bool | None
