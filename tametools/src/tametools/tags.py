from __future__ import annotations

from collections import OrderedDict
import re
from typing import Iterable


SIMPLE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TAG_TOKEN_RE = re.compile(r"^[A-Za-z0-9<>()%]+(?:_[A-Za-z0-9<>()%]+)*$")
ESCAPE_CHAR = "\\"
TAG_BLOCK_OPEN = "[["
TAG_BLOCK_CLOSE = "]]"


def normalize_tag(tag: str) -> str:
    text = str(tag).strip()
    if not text:
        return ""

    if text.startswith("<") and text.endswith(">"):
        return f"<{normalize_tag(text[1:-1])}>"

    if "(" in text and text.endswith(")"):
        prefix, suffix = text.split("(", 1)
        return f"{prefix.upper()}({suffix}"

    return text.upper()


def merge_tags(*tag_groups: Iterable[str]) -> tuple[str, ...]:
    ordered: OrderedDict[str, None] = OrderedDict()
    for tag_group in tag_groups:
        for tag in tag_group:
            normalized = normalize_tag(tag)
            if normalized:
                ordered[normalized] = None
    return tuple(ordered.keys())


def parse_header(header: object) -> tuple[str, tuple[str, ...]]:
    if header is None:
        return "", ()

    text = str(header)
    parsed = _parse_bracketed_header(text)
    if parsed is not None:
        return parsed

    if not text.startswith("_"):
        return text, ()

    return _parse_legacy_header(text)


def build_header(name: str, tags: Iterable[str]) -> str:
    merged = merge_tags(tags)
    if not merged:
        return name
    return f"{TAG_BLOCK_OPEN}{'::'.join(merged)}{TAG_BLOCK_CLOSE}{name}"


def has_all_tags(tags: Iterable[str], required: Iterable[str]) -> bool:
    tag_set = {normalize_tag(tag) for tag in tags}
    return all(normalize_tag(tag) in tag_set for tag in required)


def has_any_tag(tags: Iterable[str], candidates: Iterable[str]) -> bool:
    tag_set = {normalize_tag(tag) for tag in tags}
    return any(normalize_tag(tag) in tag_set for tag in candidates)


def _is_tag_block(text: str) -> bool:
    if not text:
        return False
    tokens = text.split("::")
    return all(TAG_TOKEN_RE.match(token) for token in tokens)


def _parse_bracketed_header(text: str) -> tuple[str, tuple[str, ...]] | None:
    if not text.startswith(TAG_BLOCK_OPEN):
        return None

    close_index = text.find(TAG_BLOCK_CLOSE, len(TAG_BLOCK_OPEN))
    if close_index < 0:
        return None

    raw_tags = text[len(TAG_BLOCK_OPEN) : close_index]
    raw_name = text[close_index + len(TAG_BLOCK_CLOSE) :]
    if not _is_tag_block(raw_tags):
        return None
    tags = [normalize_tag(tag) for tag in raw_tags.split("::") if tag]
    return raw_name, tuple(tags)


def _parse_legacy_header(text: str) -> tuple[str, tuple[str, ...]]:
    body = text[1:]
    for index in range(len(body) - 1, -1, -1):
        if body[index] != "_" or _is_escaped(body, index):
            continue
        raw_tags = body[:index]
        raw_name = body[index + 1 :]
        if _is_tag_block(raw_tags):
            tags = [normalize_tag(tag) for tag in raw_tags.split("::") if tag]
            return _unescape_header_name(raw_name), tuple(tags)
    return text, ()


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == ESCAPE_CHAR:
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _escape_header_name(name: str) -> str:
    text = str(name).replace(ESCAPE_CHAR, ESCAPE_CHAR * 2)
    return text.replace("_", ESCAPE_CHAR + "_")


def _unescape_header_name(text: str) -> str:
    chars: list[str] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] == ESCAPE_CHAR and cursor + 1 < len(text):
            chars.append(text[cursor + 1])
            cursor += 2
            continue
        chars.append(text[cursor])
        cursor += 1
    return "".join(chars)
