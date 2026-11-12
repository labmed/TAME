"""Simple questions put to the person who runs a plugin or a command.

A plugin never opens a window itself. It declares its questions as a ``Form``
and the program that runs it shows them: a dialog window on a desktop, prompts
in a terminal, and nothing at all in an unattended run, where the missing
settings are named instead. The answers come back as plain values that the
caller passes on as options or keeps as a record, so what was answered is in
the log like any other setting and a later run can repeat it without asking.

    from tametools.dialogs import field, form

    FORM = form("분석 설정", [
        field("LEVEL_ID", "수준 열", kind="column", value="id", required=True),
        field("MIN_N", "최소 자료 수", kind="integer", default=20, minimum=2),
    ])

    @register_plugin("MY_PLUGIN", form=FORM)
    def my_plugin(dataset, meta, step_name, options): ...

Only simple input is covered: text, numbers, choices, check boxes, dates, a file
and column names. A form without fields is a confirmation.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Iterator, Mapping

KINDS = ("text", "multiline", "number", "integer", "choice", "multi", "check", "date", "file", "column", "columns")
ENVIRONMENT = "TAMETOOLS_DIALOG"   # gui (default) | terminal | off: how questions are put when nothing is said on the command line
_COLUMN_KINDS = {"column": "choice", "columns": "multi"}
_CHOICE_KINDS = ("choice", "multi")
_YES = {"1", "true", "yes", "y", "on", "예", "네"}
_NO = {"0", "false", "no", "n", "off", "아니오", "아니요"}
_CLOSED: ContextVar[str] = ContextVar("tametools_dialogs_closed", default="")


class DialogUnavailable(ValueError):
    """Nobody can be asked in this run."""


class _Invalid(ValueError):
    """An answer that cannot be accepted; the message is shown to the person."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    default: Any = None
    choices: tuple[tuple[Any, str], ...] = ()     # (value handed back, text shown)
    hint: str = ""
    minimum: float | None = None
    maximum: float | None = None
    tags: tuple[str, ...] = ()                    # column kinds: only columns carrying one of these tags
    value: str = "name"                           # column kinds: hand back the column "name" or its COLUMN.ID ("id")


@dataclass(frozen=True)
class Form:
    title: str
    fields: tuple[Field, ...] = ()
    intro: str = ""
    submit: str = "확인"


def field(key: str, label: str = "", kind: str = "text", *, required: bool = False, default: Any = None,
          choices: Iterable = (), hint: str = "", minimum: float | None = None, maximum: float | None = None,
          tags: Iterable[str] = (), value: str = "name") -> Field:
    """One question. ``key`` is the option (or record field) the answer is stored under."""
    key = str(key).strip()
    if not key:
        raise ValueError("A dialog field needs a key")
    if kind not in KINDS:
        raise ValueError(f"Field {key}: unknown kind '{kind}'. Use one of: " + ", ".join(KINDS))
    pairs = tuple(_pair(item) for item in choices)
    if (kind in _CHOICE_KINDS) != bool(pairs):
        raise ValueError(f"Field {key}: choices belong to the kinds 'choice' and 'multi', which need at least one")
    if len({text for _, text in pairs}) != len(pairs):
        raise ValueError(f"Field {key}: two choices are shown with the same text")
    tags = tuple(str(tag).strip().upper() for tag in tags)
    if kind not in _COLUMN_KINDS and (tags or value != "name"):
        raise ValueError(f"Field {key}: tags and value belong to the kinds 'column' and 'columns'")
    if value not in {"name", "id"}:
        raise ValueError(f"Field {key}: value must be 'name' or 'id'")
    return Field(key, str(label or key), kind, bool(required), default, pairs, str(hint), minimum, maximum, tags, value)


def form(title: str, fields: Iterable[Field] = (), *, intro: str = "", submit: str = "확인") -> Form:
    """A titled list of questions. Without fields it is a confirmation: ``submit`` or cancel."""
    fields = tuple(fields)
    seen: set[str] = set()
    for item in fields:
        if not isinstance(item, Field):
            raise TypeError("A form is made of field(...) entries")
        if item.key.upper() in seen:
            raise ValueError(f"Field key used twice in one form: {item.key}")
        seen.add(item.key.upper())
    return Form(str(title), fields, str(intro), str(submit))


def _pair(item: Any) -> tuple[Any, str]:
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[0], str(item[1])
    return item, str(item)


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, tuple)) and len(value) == 0)


def _truth(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value if value is not None else "").strip().lower() in _YES


def _lookup(values: Mapping | None, key: str) -> Any:
    """Option names are matched without regard to case, as everywhere else in META."""
    for name, value in (values or {}).items():
        if str(name).upper() == key.upper():
            return value
    return None


# ----------------------------------------------------------------------------------------------------------------------
# What a form means: columns to choose from, starting values, accepted answers
# ----------------------------------------------------------------------------------------------------------------------

def resolved(made: Form, dataset: Any = None) -> Form:
    """The form with column questions turned into choices among the columns of ``dataset``."""
    fields = []
    for item in made.fields:
        if item.kind not in _COLUMN_KINDS:
            fields.append(item)
            continue
        if dataset is None:
            raise ValueError(f"Field {item.key}: a column question needs the dataset")
        columns = list(dataset.columns)
        if item.tags:
            tagged = {column.name for tag in item.tags for column in dataset.columns_with_tag(tag)}
            columns = [column for column in columns if column.name in tagged]
        pairs = []
        for column in columns:
            if item.value == "name":
                pairs.append((column.name, column.name))
                continue
            identifier = _lookup(dataset.column_metadata(column), "ID")
            if isinstance(identifier, str) and identifier.strip():
                pairs.append((identifier, column.name if identifier == column.name else f"{column.name} ({identifier})"))
        if not pairs:
            what = "COLUMN.ID 가 선언된 열" if item.value == "id" else "열"
            tagged = f" ({', '.join(item.tags)} 태그)" if item.tags else ""
            raise ValueError(f"'{item.label}' 에서 고를 수 있는 {what}{tagged}이 자료에 없습니다")
        fields.append(replace(item, kind=_COLUMN_KINDS[item.kind], choices=tuple(pairs), tags=(), value="name"))
    return replace(made, fields=tuple(fields))


def _listed(value: Any) -> list:
    if _empty(value):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [part.strip() for part in str(value).split(",") if part.strip()]    # the --option form: A,B,C


def _start(item: Field, value: Any) -> Any:
    if item.kind == "check":
        return _truth(value)
    if item.kind == "multi":
        return _listed(value)
    return "" if value is None else value


def initial(made: Form, values: Mapping | None = None) -> dict:
    """What each question shows before anyone answers: the current value, else the declared default."""
    start = {}
    for item in made.fields:
        current = _lookup(values, item.key)
        start[item.key] = _start(item, item.default if current is None else current)
    return start


def _known(item: Field, value: Any) -> bool:
    return any(value == choice or str(value) == str(choice) or str(value) == text for choice, text in item.choices)


def _keeping_current(made: Form, start: Mapping) -> Form:
    """A current value outside the declared choices stays selectable, so that opening and saving does not drop it."""
    fields = []
    for item in made.fields:
        current = start[item.key]
        held = current if item.kind == "multi" else [] if item.kind != "choice" or _empty(current) else [current]
        extra = [value for value in held if not _known(item, value)]
        fields.append(replace(item, choices=(*item.choices, *((value, str(value)) for value in extra))) if extra else item)
    return replace(made, fields=tuple(fields))


def _choice(item: Field, answer: Any) -> Any:
    for choice, text in item.choices:
        if answer == choice or str(answer) == str(choice) or str(answer) == text:
            return choice
    raise _Invalid(f"'{item.label}': 고를 수 없는 값입니다: {answer}")


def _number(item: Field, text: str) -> int | float:
    try:
        number: int | float = int(text)
    except ValueError:
        try:
            number = float(text)
        except ValueError:
            number = float("nan")
    if number != number or number in (float("inf"), float("-inf")) or (item.kind == "integer" and not isinstance(number, int)):
        raise _Invalid(f"'{item.label}' 에는 {'정수' if item.kind == 'integer' else '숫자'}를 적어 주세요: {text}")
    if item.minimum is not None and number < item.minimum:
        raise _Invalid(f"'{item.label}' 은(는) {item.minimum:g} 이상이어야 합니다")
    if item.maximum is not None and number > item.maximum:
        raise _Invalid(f"'{item.label}' 은(는) {item.maximum:g} 이하여야 합니다")
    return number


def _clean(item: Field, raw: Any) -> Any:
    """The accepted, typed answer to one question; ``_Invalid`` says what is wrong with it."""
    if item.kind == "check":
        return _truth(raw)
    if item.kind == "multi":
        value: Any = list(dict.fromkeys(_choice(item, answer) for answer in _listed(raw)))
    elif item.kind == "choice":
        value = "" if _empty(raw) or not str(raw).strip() else _choice(item, raw)
    elif item.kind in ("number", "integer"):
        if isinstance(raw, bool):
            raise _Invalid(f"'{item.label}' 에는 숫자를 적어 주세요")
        text = "" if raw is None else str(raw).strip()
        value = None if not text else _number(item, text)
    elif item.kind == "date":
        text = "" if raw is None else str(raw).strip()
        try:
            value = dt.date.fromisoformat(text).isoformat() if text else ""
        except ValueError as exc:
            raise _Invalid(f"'{item.label}' 은(는) YYYY-MM-DD 형식의 날짜여야 합니다: {text}") from exc
    else:
        value = "" if raw is None else str(raw).strip()
    if item.required and _empty(value):
        raise _Invalid(f"'{item.label}' 은(는) 필수 항목입니다")
    return value


def check_answers(made: Form, raw: Mapping) -> tuple[dict, dict]:
    """Typed answers, and a message for every question whose answer cannot be accepted."""
    answers, errors = {}, {}
    for item in made.fields:
        try:
            answers[item.key] = _clean(item, raw.get(item.key))
        except _Invalid as exc:
            errors[item.key] = str(exc)
    return answers, errors


def typed(made: Form, options: Mapping) -> dict:
    """Options with the values of declared questions in their declared type.

    The command line gives text (``--option MIN_COUNT=3``) where META and a dialog give typed values (``3``).
    After this a plugin sees the same type either way: numbers, true/false, lists, and the stored value of a
    choice that was named by its shown text. Text that cannot be read as the declared type is an error that
    names the option; a choice outside the declared list is left for the plugin to judge.
    """
    result = dict(options)
    for item in made.fields:
        for name, value in options.items():
            if str(name).upper() != item.key.upper() or not isinstance(value, str) or not value.strip():
                continue
            try:
                if item.kind in ("number", "integer", "date"):
                    result[name] = _clean(replace(item, required=False), value)
                elif item.kind == "check":
                    text = value.strip().lower()
                    if text not in _YES | _NO:
                        raise _Invalid(f"'{item.label}' 은(는) true 또는 false 여야 합니다: {value}")
                    result[name] = text in _YES
                elif item.kind == "multi":
                    result[name] = [_choice(item, part) if _known(item, part) else part for part in _listed(value)]
                elif item.kind == "choice" and _known(item, value.strip()):
                    result[name] = _choice(item, value.strip())
            except _Invalid as exc:
                raise ValueError(f"옵션 {name}: {exc}") from None
    return result


def missing(made: Form, values: Mapping | None = None) -> list[Field]:
    """Required questions that the current values and the declared defaults leave unanswered."""
    start = initial(made, values)
    return [item for item in made.fields if item.required and item.kind != "check" and _empty(start[item.key])]


def missing_message(lacking: Iterable[Field]) -> str:
    return ("필요한 설정이 지정되지 않았습니다: " + ", ".join(f"{item.key} ({item.label})" for item in lacking)
            + ". --option KEY=VALUE 로 지정하거나, 터미널에서 실행해 질문에 답하세요 (--dialog: 창으로 입력)")


def merged(made: Form, options: Mapping, answers: Mapping) -> dict:
    """Options with the answers put in. An answer left empty removes the option, so the plugin default applies."""
    asked = {item.key.upper() for item in made.fields}
    result = {name: value for name, value in options.items() if str(name).upper() not in asked}
    for item in made.fields:
        if item.key in answers and not _empty(answers[item.key]):
            result[item.key] = answers[item.key]
    return result


def option_text(made: Form, answers: Mapping) -> list[str]:
    """The answers as ``KEY=VALUE`` (the ``--option`` form), for the log line and for repeating the run without asking."""
    parts = []
    for item in made.fields:
        value = answers.get(item.key)
        if _empty(value):
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(part) for part in value)
        parts.append(f"{item.key}={value}")
    return parts


# ----------------------------------------------------------------------------------------------------------------------
# Who is asked, and how
# ----------------------------------------------------------------------------------------------------------------------

def preference() -> str:
    value = os.environ.get(ENVIRONMENT, "").strip().lower()
    return value if value in {"gui", "terminal", "off"} else "gui"


def someone_is_there() -> bool:
    """A person at a terminal started this run (not a pipe, a scheduler or a test)."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        if sys.platform == "win32":
            # isatty() is also true for the NUL device, which is what a scheduled task or a detached run gets as
            # stdin. Only a real console has a console mode.
            import ctypes
            import msvcrt
            mode = ctypes.c_ulong()
            handle = ctypes.c_void_p(msvcrt.get_osfhandle(sys.stdin.fileno()))
            return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
        return True
    except (AttributeError, ValueError, OSError):
        return False


def wanted(requested: bool | None, *, needed: bool) -> bool:
    """Should the questions be put? ``requested`` is --dialog (True), --no-dialog (False) or neither (None).

    Left to itself the program asks only when something required is missing and a person is at the terminal.
    """
    if requested is not None:
        return bool(requested)
    return bool(needed) and preference() != "off" and someone_is_there()


@contextmanager
def closed(reason: str) -> Iterator[None]:
    """No questions inside this block (used while a plugin computes, so that results never depend on a window)."""
    token = _CLOSED.set(reason)
    try:
        yield
    finally:
        _CLOSED.reset(token)


def ask(made: Form, values: Mapping | None = None, *, dataset: Any = None) -> dict | None:
    """Put the questions and return the typed answers, or None when the person cancels.

    Raises ``DialogUnavailable`` when there is neither a screen nor a terminal to ask on.
    """
    if _CLOSED.get():
        raise DialogUnavailable(_CLOSED.get())
    shown = resolved(made, dataset)
    start = initial(shown, values)
    shown = _keeping_current(shown, start)
    problems = []
    if preference() != "terminal":
        try:
            return _ask_gui(shown, start)
        except DialogUnavailable as exc:
            problems.append(str(exc))
    if someone_is_there():
        return _ask_terminal(shown, start)
    problems.append("터미널에서 실행한 것이 아니어서 글로도 물을 수 없습니다")
    lacking = missing(shown, values)
    raise DialogUnavailable("질문할 방법이 없습니다 (" + "; ".join(problems) + ")" + (". " + missing_message(lacking) if lacking else ""))


def confirm(question: str, *, title: str = "확인", yes: str = "예") -> bool:
    """A yes-or-cancel question."""
    return ask(form(title, intro=question, submit=yes)) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Plugins
# ----------------------------------------------------------------------------------------------------------------------

def plugin_form(spec: Any, dataset: Any, options: Mapping) -> Form | None:
    """The questions a plugin declared with ``register_plugin(form=...)``, ready to show for this dataset.

    The declaration is a ``Form``, or a function ``(dataset, options) -> Form | None`` when the questions depend on the data.
    """
    declared = getattr(spec, "form", None)
    made = declared(dataset, dict(options)) if callable(declared) else declared
    if made is None:
        return None
    if not isinstance(made, Form):
        raise TypeError(f"Plugin {getattr(spec, 'name', '')}: form must be a tametools.dialogs.Form (or a function returning one)")
    return resolved(made, dataset)


def plugin_options(spec: Any, dataset: Any, options: Mapping, *, requested: bool | None = None) -> tuple[dict, list[str] | None] | None:
    """Options for a plugin run after asking for what is missing.

    Returns ``(options, answered)``: ``answered`` lists what was set as ``KEY=VALUE`` and is None when nothing was asked.
    Returns None when the person cancelled. Raises ``DialogUnavailable`` when something required is missing and nobody
    can be asked.
    """
    made = plugin_form(spec, dataset, options)
    if made is None:
        return dict(options), None
    options = typed(made, options)      # --option gives text; the declaration says what type it is
    lacking = missing(made, options)
    if not wanted(requested, needed=bool(lacking)):
        if lacking:
            raise DialogUnavailable(missing_message(lacking))
        return dict(options), None
    answers = ask(made, options)
    if answers is None:
        return None
    return merged(made, options, answers), option_text(made, answers)


# ----------------------------------------------------------------------------------------------------------------------
# Terminal
# ----------------------------------------------------------------------------------------------------------------------

def _read_line(prompt: str) -> str:
    sys.stderr.write(prompt)      # questions go to stderr: stdout stays what the command prints
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


def _shown(item: Field, value: Any) -> str:
    if item.kind == "check":
        return "y" if value else "n"
    texts = {str(choice): text for choice, text in item.choices}
    if item.kind == "multi":
        return ", ".join(texts.get(str(part), str(part)) for part in value)
    return texts.get(str(value), str(value)) if item.kind == "choice" else str(value)


def _ask_terminal(made: Form, start: Mapping, *, read: Callable[[str], str] | None = None,
                  write: Callable[[str], None] | None = None) -> dict | None:
    read = read or _read_line
    write = write or (lambda text: print(text, file=sys.stderr))
    write(f"[{made.title}]")
    if made.intro:
        write(made.intro)
    try:
        if not made.fields:
            return {} if _truth(read(f"{made.submit}? (y/n): ").lstrip("\ufeff")) else None
        if any(not _empty(start[item.key]) for item in made.fields if item.kind != "check"):
            write("(Enter: 괄호 안의 값 그대로 · -: 비움)")
        raw = {}
        for item in made.fields:
            numbered = item.kind in _CHOICE_KINDS
            if numbered:
                for number, (_, text) in enumerate(item.choices, 1):
                    write(f"   {number}) {text}")
            current = start[item.key]
            prompt = item.label + (" *" if item.required else "")
            if item.kind == "check":
                prompt += " (y/n)"
            elif item.kind == "multi":
                prompt += " (번호를 쉼표로)"
            elif item.kind == "date" and not item.hint:
                prompt += " (YYYY-MM-DD)"
            if item.hint:
                prompt += f" ({item.hint})"
            if not _empty(current) or item.kind == "check":
                prompt += f" [{_shown(item, current)}]"
            while True:
                answer: Any = read(prompt + ": ").lstrip("\ufeff").strip()
                if answer == "":
                    answer = current
                elif answer == "-" and item.kind != "check":
                    answer = ""
                elif numbered:
                    picked = [part.strip() for part in answer.split(",") if part.strip()] if item.kind == "multi" else [answer]
                    picked = [item.choices[int(part) - 1][0] if part.isdigit() and 1 <= int(part) <= len(item.choices) else part
                              for part in picked]
                    answer = picked if item.kind == "multi" else picked[0]
                try:
                    _clean(item, answer)
                except _Invalid as exc:
                    write(f"   {exc}")
                    continue
                raw[item.key] = answer
                break
    except EOFError:
        return None
    return check_answers(made, raw)[0]


# ----------------------------------------------------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------------------------------------------------

def _sharp_text() -> None:
    """Windows draws an unaware program enlarged and blurred on a scaled display."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _cells(text: str) -> int:
    """Width of a text in character cells: Korean and other wide characters take two."""
    return sum(2 if unicodedata.east_asian_width(character) in "WF" else 1 for character in text)


class _Driver:
    """Hands of a test (or of a demonstration) on an open dialog."""

    def __init__(self, window, read, write, submit, cancel, problem):
        self.window, self._read, self._write, self.submit, self.cancel, self._problem = window, read, write, submit, cancel, problem

    def set(self, key: str, value: Any) -> None:
        self._write[key](value)

    def get(self, key: str) -> Any:
        return self._read[key]()

    @property
    def error(self) -> str:
        return self._problem.get()


def _ask_gui(made: Form, start: Mapping, *, _script: Callable[[_Driver], None] | None = None) -> dict | None:
    """The form as a dialog window. ``_script`` drives a window that is never shown (tests)."""
    if threading.current_thread() is not threading.main_thread():
        raise DialogUnavailable("창은 프로그램을 시작한 스레드에서만 엽니다")
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise DialogUnavailable("화면이 없는 환경입니다")
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError as exc:
        raise DialogUnavailable(f"이 실행 환경에는 창을 띄우는 부품(tkinter)이 없습니다: {exc}") from exc
    _sharp_text()
    root = None
    for attempt in range(3):
        # Starting Tcl/Tk now and then fails for a moment on Windows (it cannot read its own library files, most
        # often seen where standard output is being redirected); the next attempt succeeds.
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            problem = exc
            time.sleep(0.05 * (attempt + 1))
    if root is None:
        raise DialogUnavailable(f"창을 열 수 없습니다: {problem}") from problem
    if _script is not None:
        root.withdraw()
    root.title(made.title)
    root.resizable(False, False)
    body = ttk.Frame(root, padding=16)
    body.grid(sticky="nsew")
    row = 0
    if made.intro:
        ttk.Label(body, text=made.intro, wraplength=520, justify="left").grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 12))
        row += 1
    read: dict[str, Callable[[], Any]] = {}
    write: dict[str, Callable[[Any], None]] = {}
    focus: dict[str, Callable[[], Any]] = {}
    for item in made.fields:
        ttk.Label(body, text=item.label + (" *" if item.required else "")).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
        holder = ttk.Frame(body)
        holder.grid(row=row, column=1, sticky="w", pady=4)
        key = item.key
        if item.kind == "multiline":
            box = tk.Text(holder, width=46, height=4, wrap="word", font="TkDefaultFont", relief="solid", borderwidth=1)
            box.pack()
            box.bind("<Tab>", lambda event: (event.widget.tk_focusNext().focus_set(), "break")[1])
            read[key] = lambda box=box: box.get("1.0", "end").strip()
            write[key] = lambda value, box=box: (box.delete("1.0", "end"), box.insert("1.0", "" if value is None else str(value)))
            focus[key] = box.focus_set
        elif item.kind == "check":
            flag = tk.BooleanVar(value=False)
            button = ttk.Checkbutton(holder, variable=flag)
            button.pack(anchor="w")
            read[key] = flag.get
            write[key] = lambda value, flag=flag: flag.set(_truth(value))
            focus[key] = button.focus_set
        elif item.kind == "multi":
            flags = [(choice, tk.BooleanVar(value=False)) for choice, _ in item.choices]
            per_column = 6 if len(flags) > 6 else len(flags)
            buttons = []
            for index, ((_, text), (_, flag)) in enumerate(zip(item.choices, flags)):
                button = ttk.Checkbutton(holder, text=text, variable=flag)
                button.grid(row=index % per_column, column=index // per_column, sticky="w", padx=(0, 16))
                buttons.append(button)
            read[key] = lambda flags=flags: [choice for choice, flag in flags if flag.get()]
            write[key] = lambda value, flags=flags: [flag.set(any(str(choice) == str(part) for part in _listed(value))) for choice, flag in flags]
            focus[key] = buttons[0].focus_set
        elif item.kind == "choice":
            texts = ([] if item.required else [""]) + [text for _, text in item.choices]
            chosen = tk.StringVar(value="")
            widest = max(_cells(text) for text in texts)                  # long choices are shown whole, within reason
            combo = ttk.Combobox(holder, width=min(max(44, widest + 2), 76), state="readonly", values=texts, textvariable=chosen)
            combo.pack()
            by_text = {text: choice for choice, text in item.choices}
            by_value = {str(choice): text for choice, text in item.choices}
            read[key] = lambda chosen=chosen, by_text=by_text: by_text.get(chosen.get(), "")
            write[key] = lambda value, chosen=chosen, by_value=by_value, by_text=by_text: chosen.set(
                by_value.get(str(value), value if value in by_text else ""))      # by value, or by the text a person sees
            focus[key] = combo.focus_set
        else:
            typed = tk.StringVar(value="")
            entry = ttk.Entry(holder, width=38 if item.kind == "file" else 47, textvariable=typed)
            entry.pack(side="left")
            if item.kind == "file":
                def browse(typed=typed):
                    picked = filedialog.askopenfilename(parent=root)
                    if picked:
                        typed.set(picked)
                ttk.Button(holder, text="찾기…", width=7, command=browse).pack(side="left", padx=(6, 0))
            read[key] = typed.get
            write[key] = lambda value, typed=typed: typed.set("" if value is None else str(value))
            focus[key] = entry.focus_set
        write[key](start.get(key))
        row += 1
        hint = item.hint or ("YYYY-MM-DD" if item.kind == "date" else "")
        if hint:
            ttk.Label(body, text=hint, foreground="#666666", wraplength=340, justify="left").grid(row=row, column=1, sticky="w", pady=(0, 4))
            row += 1
    problem = tk.StringVar(value="")
    ttk.Label(body, textvariable=problem, foreground="#b3261e", wraplength=520, justify="left").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
    bar = ttk.Frame(body)
    bar.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
    state: dict[str, Any] = {"answers": None, "open": True}

    def close() -> None:
        state["open"] = False
        root.destroy()

    def submit() -> None:
        answers, errors = check_answers(made, {key: read[key]() for key in read})
        if errors:
            first = next(iter(errors))
            problem.set(errors[first])
            focus[first]()
            return
        state["answers"] = answers
        close()

    def on_return(event) -> None:
        if event.widget.winfo_class() != "Text":       # Enter writes a new line in a multi-line box
            submit()

    ttk.Button(bar, text="취소", command=close).pack(side="right")
    ttk.Button(bar, text=made.submit, command=submit, default="active").pack(side="right", padx=(0, 8))
    root.bind("<Return>", on_return)
    root.bind("<Escape>", lambda _event: close())
    root.protocol("WM_DELETE_WINDOW", close)
    root.update_idletasks()
    root.geometry(f"+{max((root.winfo_screenwidth() - root.winfo_width()) // 2, 0)}+{max((root.winfo_screenheight() - root.winfo_height()) // 3, 0)}")

    if _script is not None:
        def run_script() -> None:
            try:
                _script(_Driver(root, read, write, submit, close, problem))
            finally:
                if state["open"]:           # a script that leaves the window open would wait for ever
                    close()
        root.after(1, run_script)
    else:
        root.attributes("-topmost", True)                   # in front of the terminal it was started from, but not for ever
        root.after(400, lambda: state["open"] and root.attributes("-topmost", False))
        root.lift()
        root.focus_force()
        lacking = [item.key for item in made.fields if item.required and item.kind != "check" and _empty(start.get(item.key))]
        first = lacking[0] if lacking else (made.fields[0].key if made.fields else None)
        if first:
            focus[first]()
    root.mainloop()
    return state["answers"]
