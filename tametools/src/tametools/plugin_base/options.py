"""Reading plugin options whatever way they arrived.

The same option is a typed value when it comes from META or from a dialog (``5``, ``True``, ``["a", "b"]``)
and text when it comes from the command line (``--option N=5`` gives ``"5"``). ``option`` returns one type for both.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import PluginError

_YES = {"1", "true", "yes", "y", "on", "예", "네"}
_NO = {"0", "false", "no", "n", "off", "아니오", "아니요"}
_NAMES = {str: "text", int: "a whole number", float: "a number", bool: "true or false", list: "a list"}


def option(options: Mapping | None, key: str, default: Any = None, *, kind: type | None = None,
           choices: Iterable | None = None) -> Any:
    """The option ``key`` (matched without regard to case) as ``kind``, or ``default`` when it is absent or empty.

    ``kind`` is ``str``, ``int``, ``float``, ``bool`` or ``list``; left out, it is the type of ``default``
    (and the value comes back as it is when there is no default). A list also accepts the command-line form
    ``A,B,C``. A value that cannot be read as ``kind``, or is not among ``choices``, raises ``PluginError``
    naming the option.
    """
    value = None
    for name, candidate in (options or {}).items():
        if str(name).upper() == str(key).upper():
            value = candidate
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    kind = kind or (type(default) if default is not None else None)
    if kind is tuple:
        kind = list
    if kind is not None and kind not in _NAMES:
        raise PluginError(f"option(): kind must be str, int, float, bool or list, not {kind!r}")
    given = value
    try:
        if kind is bool:
            if not isinstance(value, bool):
                text = str(value).strip().lower()
                if text not in _YES | _NO:
                    raise ValueError
                value = text in _YES
        elif kind is int:
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            value = int(value) if not isinstance(value, str) else int(value.strip())
        elif kind is float:
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError
        elif kind is list:
            value = list(value) if isinstance(value, (list, tuple)) else [part.strip() for part in str(value).split(",") if part.strip()]
        elif kind is str:
            if isinstance(value, (list, tuple, dict)):
                raise ValueError
            value = str(value).strip()
    except (ValueError, TypeError):
        raise PluginError(f"Option {key} must be {_NAMES[kind]}: {given!r}") from None
    if choices is not None:
        allowed = list(choices)
        for item in (value if isinstance(value, list) else [value]):
            if item not in allowed:
                raise PluginError(f"Option {key} must be one of {', '.join(str(choice) for choice in allowed)}: {item!r}")
    return value
