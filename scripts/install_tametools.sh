#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${TAMETOOLS_EXTRAS+x}" = "x" ]; then
  EXTRAS="${TAMETOOLS_EXTRAS}"
else
  EXTRAS="report,web"
fi
PACKAGE="${ROOT}/tametools"

if [ -n "${EXTRAS}" ]; then
  PACKAGE="${PACKAGE}[${EXTRAS}]"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "오류: python3를 찾을 수 없습니다. Python 3.10 이상을 설치하세요." >&2
  exit 1
fi

if ! python3 -c 'import re, sys, setuptools; m = re.match(r"(\d+)(?:\.(\d+))?", setuptools.__version__); v = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0); sys.exit(0 if v >= (68, 0) else 1)' >/dev/null 2>&1; then
  echo "오류: setuptools 68 이상이 필요합니다." >&2
  echo "인터넷이 가능한 환경에서는 먼저 python3 -m pip install --upgrade setuptools 를 실행하세요." >&2
  exit 1
fi

echo "[tametools] 패키지 설치: ${PACKAGE}"
python3 -m pip install "${PACKAGE}" --no-build-isolation

if ! command -v tametools >/dev/null 2>&1; then
  echo "오류: tametools 명령을 PATH에서 찾을 수 없습니다." >&2
  echo "가상환경을 활성화했는지, 또는 pip 사용자 스크립트 경로가 PATH에 포함되어 있는지 확인하세요." >&2
  python3 -m pip show -f tametools || true
  exit 1
fi

tametools --help >/dev/null

echo "[tametools] 설치 완료: $(command -v tametools)"
echo "[tametools] 예: tametools info tutorial/01_tagged_eda/sample_eda.tame"
