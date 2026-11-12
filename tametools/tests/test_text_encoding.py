"""tametools reads and writes UTF-8, in files and on the standard streams.

Text in another encoding is refused with what the bytes look like and how to convert them. Converting is a
step in which the encoding is named: `validate --fix-encoding --from ...` for a .tame file, `init --encoding ...`
for a table, `[SYNC] ENCODING` for a source that keeps arriving in another encoding.
"""
import codecs
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tametools.bootstrap import init_from_table
from tametools.exceptions import TameFormatError
from tametools.io import read_tame
from tametools.provenance import log_entries
from tametools.sync import SyncError, sync_project
from tametools.text_encoding import (console_encoding, decode_named, decode_utf8, guess_encoding, is_utf8, normalized_pipe_text,
                                     usable_for_printed_text)

ROOT = Path(__file__).resolve().parents[2]
TEXT = ('<META>\n[COLUMN."검사명"]\nID = "test"\nTAGS = ["TESTNAME", "CATEGORY"]\n\n[COLUMN."결과"]\nID = "value"\nUNIT = "µmol/L"\n'
        'TAGS = ["RESULT", "NUM"]\n</META>\n<DATA>\n검사명\t결과\n크레아티닌\t80\n요산\t310\n</DATA>\n')
KOREAN = TEXT.replace("µmol/L", "umol/L")          # cp949 has no micro sign
TABLE = "검사명,결과\n혈당,95\n크레아티닌,0.9\n"


def tametools(*args, stdin=None, cwd=None, console="cp949", pipe_text=None):
    """Run the command line as another program would: output is captured as bytes, the console says `console`.

    The user's settings folder is a folder of the test, so nothing is read from or written to the real one.
    """
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONUTF8", "TAMETOOLS_PIPE_TEXT"}}
    settings = str(Path(cwd or ".") / "_user_settings")
    env.update(PYTHONIOENCODING=console, TAMETOOLS_DIALOG="off", LOCALAPPDATA=settings, XDG_CONFIG_HOME=settings,
               PYTHONPATH=os.pathsep.join([str(ROOT / "tametools" / "src"), env.get("PYTHONPATH", "")]))
    if pipe_text:
        env["TAMETOOLS_PIPE_TEXT"] = pipe_text
    return subprocess.run([sys.executable, "-m", "tametools", *args], input=stdin, capture_output=True, env=env, cwd=cwd)


# ------------------------------------------------------------------ what is UTF-8 and what is not

def test_bytes_are_recognised():
    assert is_utf8(TEXT.encode("utf-8")) and is_utf8(TEXT.encode("utf-8-sig")) and is_utf8(b"plain ascii")
    for other in ("cp949", "utf-16", "utf-32"):
        assert not is_utf8(KOREAN.encode(other))
    assert guess_encoding(KOREAN.encode("cp949")) == "cp949"
    assert guess_encoding(KOREAN.encode("utf-16")) == "utf-16"
    assert guess_encoding(b"\x81\xff\xfe") is None                       # nothing that is known reads these bytes
    assert decode_utf8(b"\xef\xbb\xbfa\r\nb\rc", "x") == "a\nb\nc"         # the mark goes, line ends become newlines
    assert decode_utf8(b"a\r\nb", "x", keep_line_ends=True) == "a\r\nb"
    assert decode_named(KOREAN.encode("cp949"), "CP949", "x") == KOREAN
    with pytest.raises(TameFormatError, match="cannot be read as ascii .line 2."):
        decode_named(KOREAN.encode("cp949"), "ascii", "x")
    with pytest.raises(TameFormatError, match="Unknown encoding: korean-ish"):
        decode_named(b"abc", "korean-ish", "x")


@pytest.mark.parametrize("encoding, seen", [("cp949", "can be read as cp949"), ("utf-16", "The bytes are UTF-16")])
def test_a_tame_file_in_another_encoding_is_refused_with_an_explanation(tmp_path, encoding, seen):
    path = tmp_path / "other.tame"
    path.write_bytes(KOREAN.encode(encoding))
    with pytest.raises(TameFormatError) as caught:
        read_tame(path)
    message = str(caught.value)
    assert "other.tame is not UTF-8 text" in message and seen in message
    assert "reads and writes UTF-8 only" in message and "validate FILE --fix-encoding --from " + encoding in message


def test_utf8_with_or_without_a_mark_reads_the_same(tmp_path):
    for encoding in ("utf-8", "utf-8-sig"):
        path = tmp_path / "ok.tame"
        path.write_bytes(TEXT.encode(encoding))
        data = read_tame(path)
        assert list(data.df.columns) == ["검사명", "결과"] and data.column_metadata("결과")["UNIT"] == "µmol/L"


# ------------------------------------------------------------------ the standard streams

def test_tame_text_written_to_standard_output_is_utf8_whatever_the_console(tmp_path):
    (tmp_path / "in.tame").write_bytes(TEXT.encode("utf-8"))
    done = tametools("convert", "in.tame", "-", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    text = done.stdout.decode("utf-8")                                     # raises if the bytes were in the console code page
    assert "µmol/L" in text and "크레아티닌" in text
    (tmp_path / "out.tame").write_bytes(done.stdout.replace(b"\r\n", b"\n"))
    assert read_tame(tmp_path / "out.tame").df["검사명"].tolist() == ["크레아티닌", "요산"]


def test_tame_text_read_from_standard_input_is_utf8(tmp_path):
    accepted = tametools("columns", "-", stdin=TEXT.encode("utf-8"), cwd=tmp_path)
    assert accepted.returncode == 0 and "검사명" in accepted.stdout.decode("utf-8")
    refused = tametools("validate", "-", stdin=KOREAN.encode("cp949"), cwd=tmp_path)
    assert refused.returncode == 1
    assert "stdin '-' is not UTF-8 text" in refused.stderr.decode("utf-8") and "cp949" in refused.stderr.decode("utf-8")


def test_printed_output_through_a_pipe_is_utf8_and_never_stops_on_a_character(tmp_path):
    (tmp_path / "table.csv").write_bytes(TABLE.encode("utf-8"))
    done = tametools("init", "table.csv", "--output", "table.tame", "--no-interactive", cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "검사명" in done.stdout.decode("utf-8")


# ------------------------------------------------------------------ converting a .tame file

def test_fix_encoding_needs_the_encoding_named_and_shows_how_the_file_would_read(tmp_path):
    (tmp_path / "old.tame").write_bytes(KOREAN.encode("cp949"))
    done = tametools("validate", "old.tame", "--fix-encoding", "--output", "new.tame", cwd=tmp_path)
    message = done.stderr.decode("utf-8")
    assert done.returncode == 1 and not (tmp_path / "new.tame").exists()
    assert "Name the encoding" in message and "Read as cp949, the file begins:" in message and "검사명" in message
    assert "add --from cp949" in message


def test_fix_encoding_writes_a_utf8_copy_records_it_and_validates_it(tmp_path):
    original = KOREAN.encode("cp949")
    (tmp_path / "old.tame").write_bytes(original)
    done = tametools("validate", "old.tame", "--fix-encoding", "--from", "cp949", "--output", "new.tame", cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    printed = done.stdout.decode("utf-8")
    assert "converted: old.tame (cp949) -> new.tame (UTF-8)" in printed and "[validate]" in printed
    assert (tmp_path / "old.tame").read_bytes() == original                # the original is kept
    assert is_utf8((tmp_path / "new.tame").read_bytes())
    data = read_tame(tmp_path / "new.tame")
    assert data.df.values.tolist() == [["크레아티닌", "80"], ["요산", "310"]] and data.column_metadata("결과")["UNIT"] == "umol/L"
    entry = next(item for item in log_entries(data) if item["OPERATION"] == "REENCODE")
    assert entry["PARAMS"]["FROM"] == "cp949" and entry["PARAMS"]["SOURCE_SHA256"] == hashlib.sha256(original).hexdigest()


def test_fix_encoding_knows_utf16_from_its_mark_and_refuses_what_does_not_fit(tmp_path):
    (tmp_path / "wide.tame").write_bytes(TEXT.encode("utf-16"))
    done = tametools("validate", "wide.tame", "--fix-encoding", "--output", "new.tame", cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert read_tame(tmp_path / "new.tame").column_metadata("결과")["UNIT"] == "µmol/L"

    (tmp_path / "old.tame").write_bytes(KOREAN.encode("cp949"))
    for arguments, message in (
        (["old.tame", "--fix-encoding", "--from", "ascii", "--output", "x.tame"], "cannot be read as ascii"),
        (["new.tame", "--fix-encoding", "--output", "x.tame"], "is UTF-8 already"),
        (["old.tame", "--fix-encoding", "--from", "cp949"], "add --output NEW.tame"),
        (["old.tame", "--fix-encoding", "--from", "cp949", "--output", "old.tame"], "--output must be another file"),
        (["old.tame", "--fix-encoding", "--from", "cp949", "--output", "x.xlsx"], "the same kind of file"),
        (["new.tame", "--from", "cp949"], "belong to --fix-encoding"),
    ):
        done = tametools("validate", *arguments, cwd=tmp_path)
        assert done.returncode == 1 and message in done.stderr.decode("utf-8"), arguments
        assert not (tmp_path / "x.tame").exists() and not (tmp_path / "x.xlsx").exists()


def test_a_sidecar_is_converted_as_text(tmp_path):
    meta = KOREAN.split("<DATA>")[0]
    (tmp_path / "old.meta.tame").write_bytes(meta.encode("cp949"))
    done = tametools("validate", "old.meta.tame", "--fix-encoding", "--from", "cp949", "--output", "new.meta.tame", cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert (tmp_path / "new.meta.tame").read_bytes().decode("utf-8") == meta


# ------------------------------------------------------------------ tables and sync sources

def test_init_reads_a_table_in_a_named_encoding_and_records_it(tmp_path):
    table = tmp_path / "table.csv"
    table.write_bytes(TABLE.encode("cp949"))
    with pytest.raises(TameFormatError, match=r"not UTF-8 text.*can be read as cp949.*init FILE --encoding cp949"):
        init_from_table(table, tmp_path / "out.tame")
    assert not (tmp_path / "out.tame").exists()
    init_from_table(table, tmp_path / "out.tame", encoding="cp949")
    data = read_tame(tmp_path / "out.tame")
    assert is_utf8((tmp_path / "out.tame").read_bytes()) and data.df["검사명"].tolist() == ["혈당", "크레아티닌"]
    assert data.meta["INFO"]["SOURCE_ENCODING"] == "cp949"
    assert next(item for item in log_entries(data) if item["OPERATION"] == "INIT")["PARAMS"]["SOURCE_ENCODING"] == "cp949"
    with pytest.raises(TameFormatError, match="cannot be read as ascii"):
        init_from_table(table, tmp_path / "other.tame", encoding="ascii")


def test_init_of_a_utf8_table_records_no_encoding(tmp_path):
    table = tmp_path / "table.csv"
    table.write_bytes(TABLE.encode("utf-8-sig"))                           # as Excel saves "CSV UTF-8"
    init_from_table(table, tmp_path / "out.tame")
    data = read_tame(tmp_path / "out.tame")
    assert list(data.df.columns) == ["검사명", "결과"] and "SOURCE_ENCODING" not in data.meta["INFO"]


PROJECT = ('<META>\n[SYNC]\nSOURCE = "log.csv"\n{extra}\n[COLUMN."검사명"]\nTAGS = ["STR"]\n\n[COLUMN."결과"]\nTAGS = ["NUM"]\n</META>\n'
           "<DATA>\n검사명\t결과\n</DATA>\n")


def test_a_sync_source_in_another_encoding_needs_that_encoding_in_the_project(tmp_path):
    (tmp_path / "log.csv").write_bytes(TABLE.encode("cp949"))
    project = tmp_path / "log.tame"
    project.write_text(PROJECT.format(extra=""), encoding="utf-8")
    with pytest.raises(SyncError, match=r'log.csv is not UTF-8 text.*\[SYNC\] ENCODING = "cp949"'):
        sync_project(project, now="2026-10-31T09:00:00+09:00")

    project.write_text(PROJECT.format(extra='ENCODING = "cp949"\n'), encoding="utf-8")
    report = sync_project(project, now="2026-10-31T09:00:00+09:00")
    assert report.status == "COMMITTED" and read_tame(project).df["검사명"].tolist() == ["혈당", "크레아티닌"]
    assert is_utf8(project.read_bytes())

    project.write_text(PROJECT.format(extra='ENCODING = "no-such-encoding"\n'), encoding="utf-8")
    with pytest.raises(SyncError, match="알 수 없는 인코딩"):
        sync_project(project, now="2026-10-31T09:00:00+09:00")


# ------------------------------------------------------------------ data and printed text on the same stream

def test_json_output_stays_one_json_value_when_a_file_is_converted(tmp_path):
    (tmp_path / "old.tame").write_bytes(KOREAN.encode("cp949"))
    done = tametools("validate", "old.tame", "--fix-encoding", "--from", "cp949", "--output", "new.tame", "--json", cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    payload = json.loads(done.stdout.decode("utf-8"))                     # nothing but the JSON value on standard output
    assert "converted: old.tame (cp949) -> new.tame (UTF-8)" in done.stderr.decode("utf-8")
    plain = json.loads(tametools("validate", "new.tame", "--json", cwd=tmp_path).stdout.decode("utf-8"))
    assert type(payload) is type(plain) and (not isinstance(plain, dict) or payload.keys() == plain.keys())


def test_printed_text_can_follow_a_code_page_while_data_stays_utf8(tmp_path):
    (tmp_path / "in.tame").write_bytes(TEXT.encode("utf-8"))
    assert "검사명" in tametools("columns", "in.tame", cwd=tmp_path).stdout.decode("utf-8")           # the default

    listed = tametools("columns", "in.tame", cwd=tmp_path, pipe_text="cp949")
    assert listed.returncode == 0 and "검사명" in listed.stdout.decode("cp949")
    with pytest.raises(UnicodeDecodeError):
        listed.stdout.decode("utf-8")
    # What is data keeps to UTF-8 on the same stream: TAME text and JSON, with characters the code page does not have.
    written = tametools("convert", "in.tame", "-", cwd=tmp_path, pipe_text="cp949").stdout.decode("utf-8")
    assert "µmol/L" in written and "크레아티닌" in written
    as_json = json.loads(tametools("columns", "in.tame", "--json", cwd=tmp_path, pipe_text="cp949").stdout.decode("utf-8"))
    assert "검사명" in json.dumps(as_json, ensure_ascii=False)
    # A character the code page lacks is replaced in printed text; the command goes on.
    shown = tametools("info", "in.tame", cwd=tmp_path, pipe_text="ascii")
    assert shown.returncode == 0 and shown.stdout.decode("ascii")


def test_piped_text_setting_names():
    assert [normalized_pipe_text(value) for value in ("UTF8", "utf-8", " Console ", "CP949", "euc_kr")] == ["utf-8", "utf-8", "console", "cp949", "euc_kr"]
    with pytest.raises(ValueError, match='"utf-8", "console" or the name of an encoding'):
        normalized_pipe_text("no-such-encoding")
    assert codecs.lookup(console_encoding())                              # whatever this machine uses, it is a known encoding


def test_doctor_shows_and_changes_how_piped_text_is_written(tmp_path):
    settings = tmp_path / "_user_settings" / "tametools" / "settings.toml"
    first = tametools("doctor", cwd=tmp_path).stdout.decode("utf-8")
    assert "[text]" in first and "files and '-' streams: utf-8 (always)" in first and "piped text: utf-8 (default)" in first
    assert not settings.exists()

    changed = tametools("doctor", "--pipe-text", "console", cwd=tmp_path).stdout.decode("utf-8", "replace")
    assert "saved: piped text = console" in changed and 'PIPE_TEXT = "console"' in settings.read_text(encoding="utf-8")
    later = tametools("doctor", cwd=tmp_path).stdout.decode("ascii", "replace")
    assert "piped text: console -> " in later and "(settings file " in later and "Back to UTF-8: tametools doctor --pipe-text utf-8" in later

    for_once = tametools("doctor", cwd=tmp_path, pipe_text="utf-8").stdout.decode("utf-8")
    assert "piped text: utf-8 (environment variable TAMETOOLS_PIPE_TEXT)" in for_once          # the variable wins over the file

    refused = tametools("doctor", "--pipe-text", "no-such-encoding", cwd=tmp_path, pipe_text="utf-8")
    assert refused.returncode == 1 and "name of an encoding" in refused.stderr.decode("utf-8") and b"[doctor]" not in refused.stdout
    assert 'PIPE_TEXT = "console"' in settings.read_text(encoding="utf-8")                      # nothing was saved

    back = tametools("doctor", "--pipe-text", "utf-8", cwd=tmp_path).stdout.decode("ascii", "replace")
    assert "saved: piped text = utf-8" in back
    assert "piped text: utf-8 (settings file " in tametools("doctor", cwd=tmp_path).stdout.decode("utf-8")


# ------------------------------------------------------------------ a setting that cannot be used never locks the program

NOT_FOR_TEXT = ("base64_codec", "hex_codec", "rot13", "zlib_codec", "undefined", "idna", "punycode", "utf-16", "utf-32",
                "utf-7", "utf-8-sig", "unicode_escape", "cp037", "no-such-encoding")


def test_only_encodings_a_console_can_show_are_accepted():
    for name in ("cp949", "euc_kr", "cp1252", "latin-1", "shift_jis", "gbk", "ascii", "utf-8"):
        assert usable_for_printed_text(name), name
    for name in NOT_FOR_TEXT:
        assert not usable_for_printed_text(name), name
        with pytest.raises(ValueError, match="name of an encoding that a console can show"):
            normalized_pipe_text(name)


@pytest.mark.parametrize("value", ["base64_codec", "undefined", "utf-16"])
def test_doctor_does_not_save_a_value_that_cannot_be_used(tmp_path, value):
    done = tametools("doctor", "--pipe-text", value, cwd=tmp_path)
    message = done.stderr.decode("utf-8")
    assert done.returncode == 1 and "that a console can show" in message
    assert message.startswith("Error: invalid input or configuration.") and "Traceback" not in message     # said plainly
    assert done.stdout == b""                                           # nothing was reported as if it had worked
    assert not (tmp_path / "_user_settings" / "tametools" / "settings.toml").exists()


@pytest.mark.parametrize("stored", ['[OUTPUT]\nPIPE_TEXT = "base64"\n', '[OUTPUT]\nPIPE_TEXT = "undefined"\n',
                                    '[OUTPUT]\nPIPE_TEXT = 17\n', "this is not a settings file [", 'OUTPUT = "text"\n'])
def test_a_wrong_setting_already_in_place_is_passed_over_and_can_be_repaired(tmp_path, stored):
    settings = tmp_path / "_user_settings" / "tametools" / "settings.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text(stored, encoding="utf-8")
    (tmp_path / "in.tame").write_bytes(KOREAN.encode("utf-8"))

    version = tametools("--version", cwd=tmp_path)
    assert version.returncode == 0 and version.stdout.decode("utf-8").startswith("tametools ")
    listed = tametools("columns", "in.tame", cwd=tmp_path)
    assert listed.returncode == 0 and "검사명" in listed.stdout.decode("utf-8")       # UTF-8 applies

    shown = tametools("doctor", cwd=tmp_path).stdout.decode("utf-8")
    assert "piped text: utf-8 (default)" in shown
    if "base64" in stored or "undefined" in stored:
        assert "ignored: settings file" in shown and "cannot be used for printed text" in shown
        assert "Repair: tametools doctor --pipe-text utf-8" in shown

    repaired = tametools("doctor", "--pipe-text", "utf-8", cwd=tmp_path)
    assert repaired.returncode == 0, repaired.stderr.decode("utf-8", "replace")
    after = tametools("doctor", cwd=tmp_path).stdout.decode("utf-8")
    assert "piped text: utf-8 (settings file " in after and "ignored:" not in after


@pytest.mark.parametrize("value", ["base64_codec", "rot13", "undefined", "idna", "utf-16", "no-such-encoding"])
def test_a_wrong_value_in_the_environment_is_passed_over_too(tmp_path, value):
    version = tametools("--version", cwd=tmp_path, pipe_text=value)
    assert version.returncode == 0 and version.stdout.decode("utf-8").startswith("tametools ")
    shown = tametools("doctor", cwd=tmp_path, pipe_text=value).stdout.decode("utf-8")
    assert "piped text: utf-8 (default)" in shown
    assert f"ignored: environment variable TAMETOOLS_PIPE_TEXT = {value!r}" in shown and "correct or remove that variable" in shown
