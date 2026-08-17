from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional

from .parsing import clean_text


def parse_html(content: bytes) -> Any:
    try:
        from bs4 import BeautifulSoup
    except ImportError as error:  # pragma: no cover - exercised by the CLI environment
        raise RuntimeError(
            "BeautifulSoup is required for HTML sources; install the package with pip install -e ."
        ) from error
    return BeautifulSoup(content, "html.parser")


def text_of(node: Any) -> str:
    if node is None:
        return ""
    return clean_text(node.get_text(" ", strip=True))


def first_node(root: Any, selectors: Iterable[str]) -> Any:
    for selector in selectors:
        node = root.select_one(selector)
        if node is not None:
            return node
    return None


def first_text(root: Any, selectors: Iterable[str]) -> str:
    return text_of(first_node(root, selectors))


def first_attr(root: Any, selectors: Iterable[str], attribute: str) -> str:
    node = first_node(root, selectors)
    return clean_text(node.get(attribute, "")) if node is not None else ""


def json_ld(root: Any) -> List[dict[str, Any]]:
    values: List[dict[str, Any]] = []
    for script in root.select('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        values.extend(item for item in candidates if isinstance(item, dict))
    return values


def _label_key(value: str) -> str:
    return clean_text(value).strip(" :№-").casefold()


def _is_label(value: str, labels: set[str]) -> bool:
    return _label_key(value) in labels


def _inline_value(value: str, labels: Iterable[str]) -> Optional[str]:
    candidate = clean_text(value)
    candidate_key = candidate.casefold()
    for label in labels:
        label_key = clean_text(label).casefold()
        if candidate_key.startswith(label_key):
            remainder = candidate[len(label) :].lstrip(" :№-")
            if remainder and _label_key(remainder) != _label_key(label):
                return remainder
    return None


def _following_value(node: Any, label_keys: set[str]) -> Optional[str]:
    parent = getattr(node, "parent", None)
    if parent is None:
        return None
    children = list(parent.children)
    try:
        position = children.index(node)
    except ValueError:
        position = -1
    if position >= 0:
        for child in children[position + 1 :]:
            candidate = text_of(child) if hasattr(child, "get_text") else clean_text(str(child))
            if candidate and not _is_label(candidate, label_keys):
                return candidate

    parent_text = text_of(parent)
    node_text = text_of(node)
    if parent_text.startswith(node_text):
        remainder = clean_text(parent_text[len(node_text) :]).lstrip(" :№-")
        if remainder and not _is_label(remainder, label_keys):
            return remainder
    return None


def labeled_value(root: Any, labels: Iterable[str]) -> Optional[str]:
    """Read a value next to a label in tables, definition lists, or sibling nodes."""

    label_list = list(labels)
    label_keys = {_label_key(label) for label in label_list}
    if not label_keys:
        return None

    for row in root.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells[:-1]):
            if _is_label(text_of(cell), label_keys):
                value = text_of(cells[index + 1])
                if value:
                    return value

    for term in root.select("dt"):
        if _is_label(text_of(term), label_keys):
            definition = term.find_next_sibling("dd")
            if definition is not None and text_of(definition):
                return text_of(definition)

    node_types = ["dt", "th", "label", "span", "div", "p", "td"]
    for node in root.find_all(node_types):
        node_text = text_of(node)
        if not node.find(True, recursive=False):
            inline = _inline_value(node_text, label_list)
            if inline:
                return inline
        if _is_label(node_text, label_keys):
            value = _following_value(node, label_keys)
            if value:
                return value

    for text_node in root.find_all(string=True):
        inline = _inline_value(str(text_node), label_list)
        if inline:
            return inline
        if _is_label(str(text_node), label_keys):
            value = _following_value(text_node, label_keys)
            if value:
                return value

    return None


def content_type_for(filename: str) -> Optional[str]:
    import mimetypes

    return mimetypes.guess_type(filename)[0]
