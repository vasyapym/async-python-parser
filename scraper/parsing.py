from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Iterable, Optional
from urllib.parse import unquote, urljoin, urlparse

_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"-?\d[\d\s]*(?:[,.]\d+)?")
_SAFE_NAME = re.compile(r"[^\w.-]+", re.UNICODE)
_COMMON_LABELS = (
    "Статус",
    "Статус закупки",
    "Дата",
    "Дата размещения",
    "Дата публикации",
    "Размещено",
    "Заказчик",
    "Наименование заказчика",
    "Окончание подачи заявок",
    "Дата и время окончания подачи заявок",
    "Начальная цена",
    "Начальная (максимальная) цена контракта",
    "НМЦК",
    "Артикул",
    "Код товара",
    "SKU",
)


def clean_text(value: Optional[str]) -> str:
    """Collapse markup whitespace without changing the readable content."""

    if not value:
        return ""
    return _WHITESPACE.sub(" ", value).strip()


def parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    """Parse Russian/European money formats such as ``1 234,50 ₽``."""

    if not value:
        return None
    match = _NUMBER.search(value.replace("\xa0", " "))
    if not match:
        return None
    number = match.group(0).replace(" ", "")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    else:
        number = number.replace(",", ".")
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse common ISO and Russian portal date formats into UTC-aware values."""

    if not value:
        return None
    candidate = clean_text(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                parsed = datetime.strptime(candidate, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def absolute_url(base_url: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    value = href.strip()
    if not value or value.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    return urljoin(base_url, value)


def filename_from_url(url: str, fallback: str = "attachment") -> str:
    path = unquote(urlparse(url).path)
    name = PurePosixPath(path).name
    return name or fallback


def safe_filename(value: str, fallback: str = "attachment") -> str:
    """Prevent traversal and filesystem-hostile names while preserving extensions."""

    name = unquote(value).replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_NAME.sub("-", name).strip(".- ")
    return (name or fallback)[:160]


def safe_component(value: str, fallback: str = "item") -> str:
    return safe_filename(value, fallback=fallback).replace(".", "_")[:100]


def unique_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def find_labeled_value(text: str, labels: Iterable[str]) -> Optional[str]:
    """Best-effort extraction for portal text rendered as ``Label: value``."""

    normalized = clean_text(text)
    label_list = list(labels)
    boundary_labels = sorted(
        set(label_list).union(_COMMON_LABELS),
        key=len,
        reverse=True,
    )
    for label in label_list:
        other_labels = [item for item in boundary_labels if item != label]
        boundary = "|".join(re.escape(item) for item in sorted(other_labels, key=len, reverse=True))
        end = r"(?=\s+(?:" + boundary + r")\s*[:№-]|$)" if boundary else r"$"
        pattern = re.compile(
            re.escape(label) + r"\s*(?:[:№-])?\s*(.{1,240}?)" + end,
            re.IGNORECASE,
        )
        match = pattern.search(normalized)
        if match:
            value = clean_text(match.group(1)).strip(" -:;")
            if value:
                return value
    return None
