#!/bin/bash
# launchdから呼ばれる夜間バッチのラッパー。ログをlogs/に残す。
set -u
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

/Users/shuyatokutake/anaconda3/bin/python3 "$PROJECT_DIR/scripts/nightly_batch.py" >> "$LOG_DIR/nightly.log" 2>&1

# ログが5MBを超えたらローテーション(1世代のみ保持)
LOG_FILE="$LOG_DIR/nightly.log"
if [ -f "$LOG_FILE" ] && [ "$(stat -f%z "$LOG_FILE")" -gt 5242880 ]; then
  mv "$LOG_FILE" "$LOG_FILE.old"
fi
