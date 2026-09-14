from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
import os
import sys
import threading
import time
import urllib.request
import webbrowser


def bundle_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def configure_paths(root: Path) -> Path:
    backend_dir = root / "web" / "backend"
    frontend_build = root / "web" / "frontend" / "build"

    if backend_dir.exists():
        sys.path.insert(0, str(backend_dir))
    os.environ.setdefault("TAMETOOLS_FRONTEND_BUILD_DIR", str(frontend_build))
    return frontend_build


def open_browser_when_ready(url: str) -> None:
    health_url = f"{url.rstrip('/')}/api/health"
    for _ in range(80):
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Start tametools Web Workbench.")
    from tametools import __version__
    parser.add_argument("--version", action="version", version=f"tametools Web Workbench {__version__}")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind. Default: 8765")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    args = parser.parse_args(argv)

    root = bundle_root()
    frontend_build = configure_paths(root)
    if not (frontend_build / "index.html").exists():
        print(
            f"error: frontend build not found at {frontend_build}. "
            "Run npm run build before packaging or rebuild tametools-web.exe.",
            file=sys.stderr,
        )
        return 2

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
