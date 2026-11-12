"""What a .tame or .xlsx file holds after a save is what was saved.

The rules of the container are checked every time a file is read and again before one is written:
a section marker is never part of a value, text outside the sections is not ignored, and a cell
keeps its blanks, its escape prefix and its leading "=".
"""
import zipfile

import openpyxl
import pytest

from tametools.cellstate import NULL, parse_serialized_cell, serialize_cell
from tametools.exceptions import TameFormatError
from tametools.io import read_tame, read_xlsx, split_tame_text, unevaluated_formulas, write_data_tame, write_tame, write_xlsx

SOURCE = """<META>
[COLUMN.id]
ID = "id"
TAGS = ["STR"]

[COLUMN.note]
ID = "note"
TAGS = ["STR"]
</META>
<DATA>
id\tnote
a\tfirst
b\tsecond
c\tthird
</DATA>
"""


@pytest.fixture
def table(tmp_path):
    def make(notes, **meta):
        path = tmp_path / "source.tame"
        path.write_text(SOURCE, encoding="utf-8")
        data = read_tame(path)
        frame = data.df.copy()
        frame["note"] = notes
        return data.replace(df=frame, meta={**data.meta, **meta})
    return make


# ------------------------------------------------------------------ section markers

@pytest.mark.parametrize("value", ["comment </DATA> continued", "see <DATA> here", "x </META> y", "<schema>", "</ job >"])
def test_a_value_with_a_section_marker_is_not_written(table, tmp_path, value):
    target = tmp_path / "out.tame"
    with pytest.raises(TameFormatError, match="section marker"):
        write_tame(target, table([value, "second", "third"]))
    assert not target.exists()                                  # nothing half-written is left behind
    with pytest.raises(TameFormatError, match="section marker"):
        write_data_tame(tmp_path / "out.data.tame", table([value, "second", "third"]))


def test_a_marker_in_meta_text_is_not_written_either(table, tmp_path):
    with pytest.raises(TameFormatError, match="section marker"):
        write_tame(tmp_path / "out.tame", table(["a", "b", "c"], INFO={"DESCRIPTION": "ends with </META> in old files"}))


def test_such_a_value_still_travels_through_xlsx(table, tmp_path):
    notes = ["comment </DATA> continued", "second", "third"]
    write_xlsx(tmp_path / "out.xlsx", table(notes))
    assert read_xlsx(tmp_path / "out.xlsx").df["note"].tolist() == notes


@pytest.mark.parametrize("text, message", [
    (SOURCE.replace("a\tfirst", "a\tcomment </DATA> continued"), r"</DATA> appears 2 times \(lines 12, 15\)"),
    (SOURCE.replace("</META>", ""), "<META> .line 1. is not closed before <DATA>"),
    (SOURCE.replace("<DATA>", ""), "</DATA> at line 14 has no <DATA> before it|text outside the sections"),
    (SOURCE.replace("</DATA>\n", ""), "<DATA> .line 10. is never closed"),
    (SOURCE + "d\tfourth\n", "text outside the sections at line 16: 'd.tfourth'"),
    ("note to self\n" + SOURCE, "text outside the sections at line 1"),
    (SOURCE + SOURCE, "appears 2 times"),
])
def test_text_that_can_be_read_in_two_ways_is_refused_on_every_read(tmp_path, text, message):
    path = tmp_path / "broken.tame"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TameFormatError, match=message):
        read_tame(path)


def test_the_error_names_the_file(tmp_path):
    path = tmp_path / "broken.tame"
    path.write_text(SOURCE + SOURCE, encoding="utf-8")
    with pytest.raises(TameFormatError, match="broken.tame"):
        read_tame(path)


def test_markers_are_recognised_as_before(tmp_path):
    lower = SOURCE.replace("<META>", "<meta>").replace("</META>", "</ meta >").replace("<DATA>", "< data >")
    for text in (SOURCE, "\ufeff" + SOURCE, "\n\n" + SOURCE + "\n\n", lower):
        path = tmp_path / "ok.tame"
        path.write_text(text, encoding="utf-8")
        assert read_tame(path).df["note"].tolist() == ["first", "second", "third"]
    assert split_tame_text("a\tb\n1\t2\n") == {}                # no markers: the caller decides what the text is
    assert split_tame_text("<DATA>a\tb\n1\t2</DATA>") == {"DATA": "a\tb\n1\t2"}


# ------------------------------------------------------------------ blanks and the escape prefix

@pytest.mark.parametrize("notes", [
    ["first", "second", "last value "],
    ["first", "second", "last value\t"],
    ["first", "middle ", " third"],
    ["first", "second", ""],
    ["first", "second", "   "],
    ["first", "second", None],
    ["\\<<NULL>>", "<<NULL>>", "\\\\<<EMPTY>>"],
    ["<<WS:2>>", "\\<<WS:2>>", "\\plain"],
    ["line one\nline two", 'say "hi"', "tab\there"],
])
def test_cells_come_back_as_they_were_saved(table, tmp_path, notes):
    data = table(notes)
    write_tame(tmp_path / "out.tame", data)
    assert read_tame(tmp_path / "out.tame").df["note"].tolist() == notes
    write_xlsx(tmp_path / "out.xlsx", data)
    assert read_xlsx(tmp_path / "out.xlsx").df["note"].tolist() == notes


def test_escaping_takes_one_prefix_off_what_it_put_on():
    for value in ("<<NULL>>", "\\<<NULL>>", "\\\\<<NULL>>", "<<ABSENT>>", "\\<<WS:3>>", "\\plain", "plain"):
        assert parse_serialized_cell(serialize_cell(value)) == value
    assert serialize_cell("\\<<NULL>>") == "\\\\<<NULL>>"
    assert parse_serialized_cell("\\<<NULL>>") == "<<NULL>>"     # as files written before read
    assert parse_serialized_cell("<<NULL>>") is NULL
    assert parse_serialized_cell("\\plain") == "\\plain"         # a backslash in front of ordinary text is just text


def test_a_blank_line_next_to_a_marker_is_not_a_row(tmp_path):
    path = tmp_path / "spaced.tame"
    path.write_text(SOURCE.replace("<DATA>\n", "<DATA>\n\n").replace("c\tthird\n", "c\tthird \n  \n"), encoding="utf-8")
    assert read_tame(path).df["note"].tolist() == ["first", "second", "third "]


# ------------------------------------------------------------------ xlsx: text is not a formula

@pytest.mark.parametrize("notes", [["=5", "10", "15"], ["=SUM(A1:A2)", ">=5", "=<5"], ["+5", "-5", "@x"]])
def test_text_that_starts_with_an_equals_sign_stays_text_in_xlsx(table, tmp_path, notes):
    write_xlsx(tmp_path / "out.xlsx", table(notes))
    sheet = openpyxl.load_workbook(tmp_path / "out.xlsx")["DATA"]
    assert [sheet.cell(row=row, column=2).data_type for row in (2, 3, 4)] == ["s", "s", "s"]
    assert read_xlsx(tmp_path / "out.xlsx").df["note"].tolist() == notes


def test_a_header_and_a_meta_line_are_text_too(table, tmp_path):
    data = table(["a", "b", "c"], INFO={"DESCRIPTION": "first line\n=second line"})
    write_xlsx(tmp_path / "out.xlsx", data)
    workbook = openpyxl.load_workbook(tmp_path / "out.xlsx")
    assert not any(cell.data_type == "f" for sheet in workbook for row in sheet.iter_rows() for cell in row)
    assert read_xlsx(tmp_path / "out.xlsx").meta["INFO"]["DESCRIPTION"] == "first line\n=second line"


def test_a_formula_without_a_stored_result_is_refused(tmp_path):
    path = tmp_path / "formula.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "DATA"
    for row in (["id", "value"], ["a", "=1+1"], ["b", 3]):
        sheet.append(row)
    other = workbook.create_sheet("notes")
    other.append(["=2+2"])
    workbook.save(path)                                        # openpyxl stores the formula and no result
    assert unevaluated_formulas(path) == ["DATA!B2", "notes!A1"]
    assert unevaluated_formulas(path, ["DATA"]) == ["DATA!B2"]
    with pytest.raises(TameFormatError, match="DATA!B2 holds a formula without a stored result"):
        read_xlsx(path)


def test_a_formula_with_a_stored_result_is_read_as_that_result(tmp_path):
    path = tmp_path / "formula.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "DATA"
    for row in (["id", "value", "text"], ["a", "=1+1", '=""'], ["b", 3, "x"]):
        sheet.append(row)
    workbook.save(path)
    # What Excel writes after calculating: the result next to the formula, also when it is empty text.
    stored = tmp_path / "stored.xlsx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stored, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                assert b'<c r="B2"><f>1+1</f><v></v></c>' in data and b'<c r="C2"><f>""</f><v></v></c>' in data
                data = data.replace(b'<c r="B2"><f>1+1</f><v></v></c>', b'<c r="B2"><f>1+1</f><v>2</v></c>')
                data = data.replace(b'<c r="C2"><f>""</f><v></v></c>', b'<c r="C2" t="str"><f>""</f><v></v></c>')
            target.writestr(item, data)
    assert unevaluated_formulas(stored) == []
    assert read_xlsx(stored).df["value"].tolist() == [2, 3]


def _with_sheet_xml(source, target, change):
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in archive.infolist():
            data = archive.read(item.filename)
            out.writestr(item, change(data) if item.filename == "xl/worksheets/sheet1.xml" else data)


def test_the_formula_check_reads_the_sheet_as_xml_however_it_is_spelled(tmp_path):
    from xml.etree import ElementTree

    plain = tmp_path / "plain.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "DATA"
    for row in (["id", "value"], ["a", "=5"], ["b", 10]):
        sheet.append(row)
    workbook.save(plain)

    def prefixed(data):                 # the same sheet as a program that names the namespace would write it
        return ElementTree.tostring(ElementTree.fromstring(data), encoding="utf-8", xml_declaration=True)

    cases = {
        "prefixed.xlsx": (prefixed, ["DATA!B2"]),
        "prefixed_stored.xlsx": (lambda data: prefixed(data).replace(b"<ns0:v />", b"<ns0:v>5</ns0:v>"), []),
        "no_value_element.xlsx": (lambda data: data.replace(b"<f>5</f><v></v>", b"<f>5</f>"), ["DATA!B2"]),
        "single_quotes_empty_text.xlsx": (lambda data: data.replace(b'<c r="B2"><f>5</f><v></v></c>', b"<c r='B2' t='str'><f>\"\"</f><v></v></c>"), []),
    }
    for name, (change, expected) in cases.items():
        path = tmp_path / name
        _with_sheet_xml(plain, path, change)
        assert b"ns0:" in zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml") or not name.startswith("prefixed")
        assert openpyxl.load_workbook(path)["DATA"]["B2"].data_type == "f"         # a formula to a spreadsheet program
        assert unevaluated_formulas(path) == expected, name
        if expected:
            with pytest.raises(TameFormatError, match="DATA!B2 holds a formula without a stored result"):
                read_xlsx(path)
    assert read_xlsx(tmp_path / "prefixed_stored.xlsx").df["value"].tolist() == [5, 10]
    assert read_xlsx(tmp_path / "single_quotes_empty_text.xlsx").df["value"].tolist() == [None, 10]


def test_the_formula_check_does_not_depend_on_how_the_sheet_part_is_stored(tmp_path):
    import re

    plain = tmp_path / "plain.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "DATA"
    for row in (["id", "value"], ["a", "=5"], ["b", 10]):
        sheet.append(row)
    workbook.save(plain)

    def utf16(data):                    # XML may be stored as UTF-16; spreadsheet programs read it
        text = re.sub(r"^<\?xml[^>]*\?>\s*", "", data.decode("utf-8"))
        return ('<?xml version="1.0" encoding="UTF-16"?>' + text).encode("utf-16")

    def named(data):                    # a namespace prefix may be written in any script
        text = re.sub(r"<(/?)([A-Za-z])", lambda found: f"<{found.group(1)}시트:{found.group(2)}", data.decode("utf-8"))
        return text.replace('<시트:worksheet xmlns="', '<시트:worksheet xmlns:시트="').encode("utf-8")

    def stored(data):
        return data.replace(b"<f>5</f><v></v>", b"<f>5</f><v>5</v>")

    for name, change, expected in (
        ("utf16.xlsx", utf16, ["DATA!B2"]),
        ("utf16_stored.xlsx", lambda data: utf16(stored(data)), []),
        ("named.xlsx", named, ["DATA!B2"]),
        ("named_stored.xlsx", lambda data: named(stored(data)), []),
    ):
        path = tmp_path / name
        _with_sheet_xml(plain, path, change)
        assert openpyxl.load_workbook(path)["DATA"]["B2"].data_type == "f"
        assert unevaluated_formulas(path) == expected, name
        if expected:
            with pytest.raises(TameFormatError, match="DATA!B2 holds a formula without a stored result"):
                read_xlsx(path)
        else:
            assert read_xlsx(path).df["value"].tolist() == [5, 10]
