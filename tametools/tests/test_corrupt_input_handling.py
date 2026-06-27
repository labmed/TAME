from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tametools.cli import main as cli_main


class CorruptInputHandlingTest(unittest.TestCase):
    def _run_cli(self, *args: str) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["tametools", *args]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli_main()
        return code, stdout.getvalue() + stderr.getvalue()

    def test_common_corrupt_inputs_fail_without_python_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "missing.tame"
            malformed_meta = root / "malformed_meta.tame"
            missing_data = root / "missing_data.tame"
            invalid_utf8 = root / "invalid_utf8.tame"
            fake_xlsx = root / "fake.xlsx"
            unsupported = root / "notes.txt"

            malformed_meta.write_text("<META>\n[bad\n</META>\n<DATA>\na\n1\n</DATA>", encoding="utf-8")
            missing_data.write_text("<META>\n[INFO]\nNAME='x'\n</META>", encoding="utf-8")
            invalid_utf8.write_bytes(b"<DATA>\ncol\n\xff\n</DATA>")
            fake_xlsx.write_bytes(b"not a zip workbook")
            unsupported.write_text("plain text", encoding="utf-8")

            cases = [
                ("missing", ("validate", str(missing))),
                ("malformed_meta", ("review", str(malformed_meta))),
                ("missing_data", ("columns", str(missing_data))),
                ("invalid_utf8", ("info", str(invalid_utf8))),
                ("fake_xlsx", ("validate", str(fake_xlsx))),
                ("unsupported", ("info", str(unsupported))),
            ]
            outputs = {name: self._run_cli(*args) for name, args in cases}

        for name, (code, output) in outputs.items():
            with self.subTest(name=name):
                self.assertNotEqual(code, 0)
                self.assertIn("Error:", output)
                self.assertIn("--debug", output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("FileNotFoundError", output)
                self.assertNotIn("TOMLDecodeError", output)
                self.assertNotIn("BadZipFile", output)


if __name__ == "__main__":
    unittest.main()
