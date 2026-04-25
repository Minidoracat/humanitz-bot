#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

SAVE_PATH="${SAVE_FILE_PATH:-/home/hzserver/serverfiles/HumanitZServer/Saved/SaveGames/SaveList/Default/Save_DedicatedSaveMP.sav}"
OUTPUT="${SAVE_CACHE_PATH:-/home/hzserver/humanitz-bot/tmp/save-cache-lite.json}"
LOCK_FILE="${SAVE_CACHE_LOCK:-/home/hzserver/humanitz-bot/tmp/save-cache-lite.lock}"

mkdir -p "$(dirname "$OUTPUT")" "$(dirname "$LOCK_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[save-cache-lite] another cache refresh is already running"
    exit 0
fi

RUNNER=(nice -n 10)
if command -v ionice >/dev/null 2>&1; then
    RUNNER=(ionice -c2 -n7 "${RUNNER[@]}")
fi

exec "${RUNNER[@]}" node scripts/save-cache-lite.js \
    --save "$SAVE_PATH" \
    --output "$OUTPUT"
