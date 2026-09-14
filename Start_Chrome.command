#!/bin/bash
APP_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
bash "$APP_ROOT/Start_Chrome.sh" "$@"
result=$?
if [ "$result" -ne 0 ]; then
  read -r -p 'Press Enter to close. '
fi
exit "$result"
