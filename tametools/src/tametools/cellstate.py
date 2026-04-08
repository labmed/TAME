from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

import pandas as pd

from .config import ci_get


STATE_ABSENT = "ABSENT"
STATE_NULL = "NULL"
STATE_EMPTY = "EMPTY"
STATE_WS = "WS"
STATE_VALUE = "VALUE"

DEFAULT_ABSENT_TOKEN = "<<ABSENT>>"
DEFAULT_NULL_TOKEN = "<<NULL>>"
DEFAULT_EMPTY_TOKEN = "<<EMPTY>>"
DEFAULT_WS_TOKEN_PREFIX = "<<WS:"
DEFAULT_TOKEN_SUFFIX = ">>"
DEFAULT_ESCAPE_PREFIX = "\\"


@dataclass(frozen=True)
class NullCellValue:
    def __str__(self) -> str:
        return DEFAULT_NULL_TOKEN

    def __repr__(self) -> str:
        return str(self)


NULL = NullCellValue()


@dataclass(frozen=True)
class CellTokenConfig:
    absent_token: str = DEFAULT_ABSENT_TOKEN
    null_token: str = DEFAULT_NULL_TOKEN
    empty_token: str = DEFAULT_EMPTY_TOKEN
    ws_token_prefix: str = DEFAULT_WS_TOKEN_PREFIX
    token_suffix: str = DEFAULT_TOKEN_SUFFIX
    escape_prefix: str = DEFAULT_ESCAPE_PREFIX


def token_config(mapping: Mapping[str, Any] | None) -> CellTokenConfig:
    return CellTokenConfig(
        absent_token=str(ci_get(mapping, "ABSENT_TOKEN", DEFAULT_ABSENT_TOKEN)),
        null_token=str(ci_get(mapping, "NULL_TOKEN", DEFAULT_NULL_TOKEN)),
        empty_token=str(ci_get(mapping, "EMPTY_TOKEN", DEFAULT_EMPTY_TOKEN)),
        ws_token_prefix=str(ci_get(mapping, "WS_TOKEN_PREFIX", DEFAULT_WS_TOKEN_PREFIX)),
        token_suffix=str(ci_get(mapping, "TOKEN_SUFFIX", DEFAULT_TOKEN_SUFFIX)),
        escape_prefix=str(ci_get(mapping, "ESCAPE_PREFIX", DEFAULT_ESCAPE_PREFIX)),
    )


def parse_serialized_cell(value: Any, mapping: Mapping[str, Any] | None = None) -> Any:
    if _is_absent_value(value):
        return None
    if not isinstance(value, str):
        return value
    if value == "":
        return None

    cfg = token_config(mapping)
    if value.startswith(cfg.escape_prefix):
        unescaped = value[len(cfg.escape_prefix) :]
        if is_reserved_token(unescaped, cfg):
            return unescaped

    if value == cfg.absent_token:
        return None
    if value == cfg.null_token:
        return NULL
    if value == cfg.empty_token:
        return ""

    ws_length = parse_ws_token_length(value, cfg)
    if ws_length is not None:
        return " " * ws_length

    return value


def serialize_cell(value: Any, mapping: Mapping[str, Any] | None = None, *, for_excel: bool = False) -> Any:
    cfg = token_config(mapping)
    state = cell_state(value)
    if state == STATE_ABSENT:
        return cfg.absent_token
    if state == STATE_NULL:
        return cfg.null_token
    if state == STATE_EMPTY:
        return cfg.empty_token
    if state == STATE_WS:
        return f"{cfg.ws_token_prefix}{len(str(value))}{cfg.token_suffix}"
    if isinstance(value, str) and is_reserved_token(value, cfg):
        return f"{cfg.escape_prefix}{value}"
    return value


def cell_state(value: Any) -> str:
    if _is_absent_value(value):
        return STATE_ABSENT
    if value is NULL or isinstance(value, NullCellValue):
        return STATE_NULL
    if isinstance(value, str):
        if value == "":
            return STATE_EMPTY
        if value.strip() == "":
            return STATE_WS
    return STATE_VALUE


def is_reserved_token(text: str, cfg: CellTokenConfig | None = None) -> bool:
    cfg = cfg or CellTokenConfig()
    return text in {cfg.absent_token, cfg.null_token, cfg.empty_token} or parse_ws_token_length(text, cfg) is not None


def parse_ws_token_length(text: str, cfg: CellTokenConfig | None = None) -> int | None:
    cfg = cfg or CellTokenConfig()
    pattern = re.escape(cfg.ws_token_prefix) + r"(?P<count>\d+)" + re.escape(cfg.token_suffix) + r"$"
    match = re.match(pattern, text)
    if not match:
        return None
    return int(match.group("count"))


def state_counts(series: pd.Series) -> dict[str, int]:
    counts = {STATE_ABSENT: 0, STATE_NULL: 0, STATE_EMPTY: 0, STATE_WS: 0, STATE_VALUE: 0}
    for value in series.tolist():
        counts[cell_state(value)] += 1
    return counts


def _is_absent_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, NullCellValue):
        return False
    try:
        return bool(pd.isna(value))
    except Exception:
        return False
