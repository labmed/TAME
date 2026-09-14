from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tametools" / "src"))
sys.path.insert(0, str(ROOT / "web" / "backend"))

from starlette.staticfiles import StaticFiles

from app.main import FRONTEND_BUILD_DIR, app


class WebMainStaticTests(unittest.TestCase):
    @unittest.skipUnless((FRONTEND_BUILD_DIR / "index.html").exists(), "frontend production build is absent")
    def test_backend_mounts_built_frontend_after_api_routes(self) -> None:
        index = (FRONTEND_BUILD_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("tametools Workbench", index)
        self.assertIn("/_app/immutable/", index)

        frontend_routes = [route for route in app.routes if getattr(route, "name", "") == "frontend"]
        self.assertEqual(len(frontend_routes), 1)
        self.assertIsInstance(frontend_routes[0].app, StaticFiles)

        api_route_indexes = [index for index, route in enumerate(app.routes) if str(getattr(route, "path", "")).startswith("/api/")]
        frontend_index = app.routes.index(frontend_routes[0])
        self.assertTrue(api_route_indexes)
        self.assertLess(max(api_route_indexes), frontend_index)


if __name__ == "__main__":
    unittest.main()
