#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3.10–3.13 64-bit is required. See README_BROWSER_KO.md.' >&2
  exit 1
fi
exec python3 "$APP_ROOT/scripts/start_tametools_browser.py" "$@"
