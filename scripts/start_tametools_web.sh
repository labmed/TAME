#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${TAMETOOLS_BACKEND_PORT:-8765}"
FRONTEND_PORT="${TAMETOOLS_FRONTEND_PORT:-5173}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
FRONTEND_BUILD_INDEX="${ROOT}/web/frontend/build/index.html"

echo "[tametools] 저장소: ${ROOT}"
echo "[tametools] 웹앱을 시작합니다."

if ! command -v python3 >/dev/null 2>&1; then
  echo "오류: python3를 찾을 수 없습니다. Python 3.10 이상을 설치하세요." >&2
  exit 1
fi

if [ ! -d "${ROOT}/wheelhouse" ] && ! python3 -c 'import re, sys, setuptools; m = re.match(r"(\d+)(?:\.(\d+))?", setuptools.__version__); v = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0); sys.exit(0 if v >= (68, 0) else 1)' >/dev/null 2>&1; then
  echo "오류: setuptools 68 이상이 필요합니다." >&2
  echo "인터넷이 가능한 환경에서는 먼저 python3 -m pip install --upgrade setuptools 를 실행하세요." >&2
  exit 1
fi

PACKAGE_EXTRAS="${TAMETOOLS_WEB_EXTRAS:-web,report,analysis,nhanes}"
if [ -d "${ROOT}/wheelhouse" ]; then
  python3 -m pip install --no-index --find-links "${ROOT}/wheelhouse" "tametools[${PACKAGE_EXTRAS}]"
elif [ "${TAMETOOLS_EDITABLE:-0}" = "1" ]; then
  python3 -m pip install -e "${ROOT}/tametools[${PACKAGE_EXTRAS}]" --no-build-isolation
else
  python3 -m pip install "${ROOT}/tametools[${PACKAGE_EXTRAS}]" --no-build-isolation
fi

NEED_FRONTEND_BUILD=0
if [ "${TAMETOOLS_REBUILD_FRONTEND:-0}" = "1" ] || [ ! -f "${FRONTEND_BUILD_INDEX}" ]; then
  NEED_FRONTEND_BUILD=1
fi

if [ "${TAMETOOLS_DEV_FRONTEND:-0}" = "1" ] || [ "${NEED_FRONTEND_BUILD}" = "1" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "오류: 프론트엔드 build가 필요하지만 npm을 찾을 수 없습니다." >&2
    echo "Node.js LTS를 설치하거나, build/가 포함된 배포 번들을 사용하세요." >&2
    exit 1
  fi
  if [ ! -d "${ROOT}/web/frontend/node_modules" ]; then
    echo "[tametools] 프론트엔드 의존성을 설치합니다."
    (cd "${ROOT}/web/frontend" && npm install)
  fi
fi

if [ "${NEED_FRONTEND_BUILD}" = "1" ]; then
  echo "[tametools] 프론트엔드 프로덕션 빌드를 생성합니다."
  (cd "${ROOT}/web/frontend" && VITE_TAMETOOLS_API_BASE="" npm run build)
fi

cleanup() {
  if [ -n "${BACKEND_PID:-}" ]; then kill "${BACKEND_PID}" >/dev/null 2>&1 || true; fi
  if [ -n "${FRONTEND_PID:-}" ]; then kill "${FRONTEND_PID}" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT INT TERM

echo "[tametools] 백엔드 시작: ${BACKEND_URL}"
(
  cd "${ROOT}/web/backend"
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${BACKEND_PORT}"
) &
BACKEND_PID=$!

if [ "${TAMETOOLS_DEV_FRONTEND:-0}" = "1" ]; then
  echo "[tametools] 개발용 프론트엔드 시작: ${FRONTEND_URL}"
  (
    cd "${ROOT}/web/frontend"
    VITE_TAMETOOLS_API_BASE="${BACKEND_URL}" npm run dev -- --port "${FRONTEND_PORT}"
  ) &
  FRONTEND_PID=$!
else
  FRONTEND_URL="${BACKEND_URL}"
  echo "[tametools] 프론트엔드는 백엔드에서 정적 서빙합니다: ${FRONTEND_URL}"
fi

sleep 4
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${FRONTEND_URL}" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "${FRONTEND_URL}" >/dev/null 2>&1 || true
elif command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /c start "${FRONTEND_URL}" >/dev/null 2>&1 || true
fi

echo "[tametools] 브라우저에서 ${FRONTEND_URL} 를 여세요."
echo "[tametools] 종료하려면 이 창에서 Ctrl+C를 누르세요."
wait
