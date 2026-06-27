"""드롭인 플러그인 폴더 자동탐색 회귀 테스트.

사용자 쓰기가능 디렉터리(TAMETOOLS_PLUGIN_PATH)에 .py 를 넣으면 --allow-plugins 시 발견되고,
빌트인은 보호되며, 깨진 파일은 전체를 막지 않고 기록만 되는지 고정한다. (frozen exe 호환을 위해
점 모듈명/PYTHONPATH 없이 파일 경로로 로드한다.)
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tametools.plugin_base import manager
from tametools.plugin_base.base import PLUGIN_REGISTRY

GOOD_PLUGIN = """
from tametools.models import OperationOutput
from tametools.plugin_base.base import register_plugin


@register_plugin("DROPIN_DEMO", description="dropped-in demo plugin")
def handler(dataset, meta, step_name, options):
    return OperationOutput(name=step_name, table=dataset.df, message="dropin ok")
"""


class DropinPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "demo_plugin.py").write_text(GOOD_PLUGIN, encoding="utf-8")
        self._old_env = os.environ.get("TAMETOOLS_PLUGIN_PATH")
        os.environ["TAMETOOLS_PLUGIN_PATH"] = str(self.dir)
        self._reset()

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("TAMETOOLS_PLUGIN_PATH", None)
        else:
            os.environ["TAMETOOLS_PLUGIN_PATH"] = self._old_env
        self._reset()

    @staticmethod
    def _reset() -> None:
        manager._EXTERNAL_LOADED = False
        PLUGIN_REGISTRY.pop("DROPIN_DEMO", None)
        for name, spec in manager._TRUSTED_PLUGINS.items():
            PLUGIN_REGISTRY[name] = spec

    def test_discovered_only_with_allow_external(self) -> None:
        # 기본(게이트 닫힘)에서는 드롭인이 보이지 않는다.
        self.assertIsNone(manager.get_plugin("DROPIN_DEMO", {}, allow_external=False))
        # --allow-plugins 상당(allow_external=True)에서만 발견된다.
        self._reset()
        spec = manager.get_plugin("DROPIN_DEMO", {}, allow_external=True)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.description, "dropped-in demo plugin")

    def test_listed_with_allow_external(self) -> None:
        names = {spec.name for spec in manager.list_plugins({}, allow_external=True)}
        self.assertIn("DROPIN_DEMO", names)

    def test_broken_file_does_not_crash_others(self) -> None:
        (self.dir / "broken.py").write_text("import a_module_that_does_not_exist_xyz\n", encoding="utf-8")
        self._reset()
        spec = manager.get_plugin("DROPIN_DEMO", {}, allow_external=True)
        self.assertIsNotNone(spec)  # 정상 플러그인은 여전히 로드된다
        errors = manager.plugin_load_errors()
        self.assertTrue(any("broken.py" in error for error in errors), errors)

    def test_builtin_is_not_overridden(self) -> None:
        (self.dir / "evil.py").write_text(
            textwrap.dedent(
                """
                from tametools.models import OperationOutput
                from tametools.plugin_base.base import register_plugin

                @register_plugin("REFERENCE_INTERVAL")
                def handler(dataset, meta, step_name, options):
                    return OperationOutput(name=step_name, table=dataset.df, message="hijacked")
                """
            ),
            encoding="utf-8",
        )
        self._reset()
        spec = manager.get_plugin("REFERENCE_INTERVAL", {}, allow_external=True)
        self.assertIsNotNone(spec)
        # 동봉(신뢰) 플러그인이 유지된다(외부 드롭인이 덮어쓰지 못함).
        self.assertIn("reference_interval", spec.handler.__module__)
        self.assertNotIn("evil", spec.handler.__module__)
        self.assertTrue(any("collides with a built-in plugin" in error for error in manager.plugin_load_errors()))

    def test_meta_module_builtin_collision_is_not_allowed_after_external_scan(self) -> None:
        # META 점 모듈은 호출마다 import 되므로, 외부 폴더 스캔이 이미 끝난 뒤에도 동봉 이름을 보호해야 한다.
        self.assertIsNotNone(manager.get_plugin("DROPIN_DEMO", {}, allow_external=True))

        module_name = "evil_meta_plugin"
        module_dir = Path(tempfile.mkdtemp())
        (module_dir / f"{module_name}.py").write_text(
            textwrap.dedent(
                """
                from tametools.models import OperationOutput
                from tametools.plugin_base.base import register_plugin

                @register_plugin("REFERENCE_INTERVAL")
                def handler(dataset, meta, step_name, options):
                    return OperationOutput(name=step_name, table=dataset.df, message="hijacked by meta")
                """
            ),
            encoding="utf-8",
        )
        sys.path.insert(0, str(module_dir))
        try:
            spec = manager.get_plugin(
                "REFERENCE_INTERVAL",
                {"PLUGINS": {"MODULES": [module_name]}},
                allow_external=True,
            )
        finally:
            if str(module_dir) in sys.path:
                sys.path.remove(str(module_dir))
            sys.modules.pop(module_name, None)

        self.assertIsNotNone(spec)
        self.assertIn("reference_interval", spec.handler.__module__)
        self.assertNotIn(module_name, spec.handler.__module__)
        self.assertTrue(
            any(
                module_name in error and "collides with a built-in plugin" in error
                for error in manager.plugin_load_errors()
            )
        )


if __name__ == "__main__":
    unittest.main()
