from __future__ import annotations

from pathlib import Path
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


class PackagingEntrypointTests(unittest.TestCase):
    def test_runtime_and_project_versions_match(self) -> None:
        from tametools import __version__
        from tametools.cli import _version_text
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertEqual(__version__, data["project"]["version"])
        self.assertEqual(_version_text(), f"tametools {__version__}")

    def test_pyproject_declares_tametools_console_script(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        scripts = data["project"]["scripts"]
        self.assertEqual(scripts["tametools"], "tametools.cli:main")

    def test_console_script_target_is_importable(self) -> None:
        from tametools.cli import main

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
