"""Text that tametools reads and writes is UTF-8.

A file in another encoding is not guessed at. Reading stops and says what the bytes look like and how to
convert them; converting is a separate step in which the encoding is named.
"""
from __future__ import annotations

import codecs
import os
from pathlib import Path
import sys

from .exceptions import TameFormatError

LIKELY_ENCODINGS = ("cp949",)        # tried in this order to describe bytes that are not UTF-8
_MARKS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
TAME_HINT = "To convert a .tame file: tametools validate FILE --fix-encoding --from {encoding} --output NEW.tame"
TABLE_HINT = "Name the encoding of the table: tametools init FILE --encoding {encoding} --output NEW.tame"
SAVE_HINT = "Save the file as UTF-8 (in Excel: CSV UTF-8) and run again."


def guess_encoding(data: bytes) -> str | None:
    """A name for bytes that are not UTF-8: from a byte-order mark, else the first likely encoding that reads them all."""
    for mark, name in _MARKS:
        if data.startswith(mark):
            return name
    for name in LIKELY_ENCODINGS:
        try:
            data.decode(name)
        except UnicodeDecodeError:
            continue
        return name
    return None


def _first_bad_byte(data: bytes) -> int | None:
    if data.startswith(tuple(mark for mark, _ in _MARKS)) or b"\x00" in data[:4096]:
        return 0                       # UTF-16/32, or not text at all
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        return error.start
    return None


def is_utf8(data: bytes) -> bool:
    return _first_bad_byte(data) is None


def not_utf8_message(data: bytes, source: str, hint: str = TAME_HINT) -> str:
    position = _first_bad_byte(data) or 0
    guess = guess_encoding(data)
    if guess in {"utf-16", "utf-32"}:
        seen = f"The bytes are {guess.upper()}."
    elif guess:
        seen = f"The bytes can be read as {guess}" + (" (the Korean Windows code page)." if guess == "cp949" else ".")
    else:
        seen = "The encoding could not be identified."
    return (f"{source} is not UTF-8 text (first byte that does not fit: line {data.count(bytes([10]), 0, position) + 1}). {seen} "
            "tametools reads and writes UTF-8 only. " + hint.format(encoding=guess or "ENCODING"))


def _line_ends(text: str, keep: bool) -> str:
    return text if keep else text.replace("\r\n", "\n").replace("\r", "\n")


def decode_utf8(data: bytes, source: str, *, hint: str = TAME_HINT, keep_line_ends: bool = False) -> str:
    """The text of UTF-8 bytes, with line ends as newline characters. A byte-order mark is accepted and dropped."""
    if not is_utf8(data):
        raise TameFormatError(not_utf8_message(data, source, hint))
    return _line_ends(data.decode("utf-8-sig"), keep_line_ends)


def read_utf8(path: str | Path, *, hint: str = TAME_HINT) -> str:
    return decode_utf8(Path(path).read_bytes(), str(path), hint=hint)


def decode_named(data: bytes, encoding: str, source: str, *, keep_line_ends: bool = False) -> str:
    """The text of bytes in an encoding the user named. Nothing is replaced: bytes that do not fit are an error."""
    try:
        codecs.lookup(encoding)
    except LookupError:
        raise TameFormatError(f"Unknown encoding: {encoding}. Examples: cp949, euc-kr, utf-16, shift_jis") from None
    try:
        text = data.decode("utf-8-sig" if codecs.lookup(encoding).name == "utf-8" else encoding)
    except UnicodeError as error:
        start = getattr(error, "start", 0) or 0
        raise TameFormatError(f"{source} cannot be read as {encoding} (line {data.count(bytes([10]), 0, start) + 1}). "
                              "Check the encoding name; nothing was converted.") from None
    return _line_ends(text.lstrip(chr(0xFEFF)), keep_line_ends)


PIPE_TEXT_VARIABLE = "TAMETOOLS_PIPE_TEXT"
PIPE_TEXT_DEFAULT = "utf-8"


def user_settings_path() -> Path:
    """The file that holds this user's tametools settings."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tametools" / "settings.toml"


def read_user_settings() -> dict:
    """The user's settings. A file that is absent or cannot be read is the same as no settings."""
    from .toml_compat import loads
    try:
        value = loads(user_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


_PROBE = "tametools 0.5 abc XYZ 09 ~|\\ \t\n"


def usable_for_printed_text(name: str) -> bool:
    """Whether printed text can be written in this encoding and read by a console.

    Python knows codecs that are no text encodings at all (base64, zlib), some that fail when used (undefined, idna)
    and some that change plain ASCII (UTF-16, unicode_escape). A console code page does none of that: ASCII stays as
    it is, and a character it lacks can be replaced.
    """
    try:
        info = codecs.lookup(str(name))
        if not getattr(info, "_is_text_encoding", True):
            return False
        return _PROBE.encode(info.name, "replace") == _PROBE.encode("ascii") and isinstance("가µ".encode(info.name, "replace"), bytes)
    except Exception:                  # whatever a codec does when it is asked for something it cannot do
        return False


def normalized_pipe_text(value: str) -> str:
    """"utf-8", "console", or the name of an encoding that a console can show, such as cp949."""
    text = str(value).strip().lower()
    if text in {"utf-8", "utf8"}:
        return "utf-8"
    if text == "console":
        return "console"
    if not usable_for_printed_text(text):
        raise ValueError(f'Piped text is "utf-8", "console" or the name of an encoding that a console can show '
                         f'(for example cp949), not {value!r}')
    return codecs.lookup(text).name


def _pipe_text() -> tuple[str, str, list[str]]:
    """The setting, where it was decided, and the values that were set but cannot be used."""
    stored = read_user_settings().get("OUTPUT")
    candidates = ((os.environ.get(PIPE_TEXT_VARIABLE), f"environment variable {PIPE_TEXT_VARIABLE}"),
                  (stored.get("PIPE_TEXT") if isinstance(stored, dict) else None, f"settings file {user_settings_path()}"))
    ignored: list[str] = []
    for value, source in candidates:
        if value in (None, ""):
            continue
        try:
            return normalized_pipe_text(value), source, ignored
        except ValueError:
            ignored.append(f"{source} = {value!r}")     # an unusable value never stops a command; UTF-8 applies instead
    return PIPE_TEXT_DEFAULT, "default", ignored


def pipe_text_setting() -> tuple[str, str]:
    """How printed text is encoded when it does not go to a console, and where that was decided.

    The default is UTF-8. "console" follows the console code page, which is what Windows PowerShell 5.1 expects
    from a program in a pipeline. The environment variable wins over the settings file (`tametools doctor --pipe-text`).
    A value that cannot be used for printed text is passed over, so a wrong setting can always be repaired.
    """
    return _pipe_text()[:2]


def ignored_pipe_text() -> list[str]:
    """Settings that are in place but cannot be used, as "where = value" (for `tametools doctor`)."""
    return _pipe_text()[2]


def save_pipe_text(value: str) -> Path:
    """Store the setting for this user and return the file it was written to."""
    from .toml_compat import dumps
    settings = read_user_settings()
    output = settings.get("OUTPUT") if isinstance(settings.get("OUTPUT"), dict) else {}
    output["PIPE_TEXT"] = normalized_pipe_text(value)
    settings["OUTPUT"] = output
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(settings), encoding="utf-8")
    return path


def console_encoding() -> str:
    """The encoding a console program is expected to write on this machine: on Windows the console output code page."""
    name = ""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            page = ctypes.windll.kernel32.GetConsoleOutputCP()
        except Exception:              # no console API here
            page = 0
        if page:
            name = "utf-8" if page == 65001 else f"cp{page}"
    if not name:
        import locale
        name = locale.getpreferredencoding(False) or "utf-8"
    return codecs.lookup(name).name if usable_for_printed_text(name) else "utf-8"


def utf8_standard_streams() -> None:
    """Make the standard streams follow the same rule as files.

    On a console Python already writes Unicode, and a console that cannot show a character gets a replacement mark
    instead of stopping the command. Text that is piped or redirected is UTF-8 unless the user chose otherwise
    (see pipe_text_setting). Data on these streams, TAME text and JSON, is always UTF-8: it is written as bytes by
    write_utf8_to_standard_output and read by read_standard_input, whatever the setting for printed text.
    """
    # Nothing here may stop a command: this runs before the command is even read, and with a setting that
    # breaks it there would be no way left to run the command that repairs the setting.
    try:
        encoding, _ = pipe_text_setting()
        if encoding == "console":
            encoding = console_encoding()
    except Exception:
        encoding = PIPE_TEXT_DEFAULT
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue                   # a caller put its own text object in place
        try:
            if stream.isatty():
                if stream is not sys.stdin:
                    reconfigure(errors="replace")
            elif stream is sys.stdin:
                reconfigure(encoding="utf-8", errors="strict")
            else:
                try:
                    reconfigure(encoding=encoding, errors="replace")
                except LookupError:
                    reconfigure(encoding=PIPE_TEXT_DEFAULT, errors="replace")
        except Exception:
            pass                       # a closed or unusual stream: leave it as it is


def write_utf8_to_standard_output(text: str) -> None:
    """Data on standard output (TAME text, JSON) as UTF-8 bytes, whatever the setting for printed text."""
    stream = sys.stdout
    buffer = getattr(stream, "buffer", None)
    try:
        to_console = stream.isatty()
    except ValueError:
        to_console = False
    if buffer is None or to_console:
        stream.write(text)             # a console shows Unicode; a caller's own text object takes text
        return
    stream.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def read_standard_input(what: str = "standard input") -> str:
    """UTF-8 text from standard input; bytes in another encoding are refused with the same explanation as a file."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()        # a text object supplied by a caller
    return decode_utf8(buffer.read(), what, hint="Pipe UTF-8 text, or convert the file first.")
