#!/usr/bin/env bash
# Remove the launchd agents. Use --dry-run first.
set -u
. "$(dirname "$0")/_agents.sh"
for name in $AGENTS; do
  run launchctl bootout "gui/$(id -u)/com.dailyjobtool.$name" 2>/dev/null || true
  run rm -f "$LA_DIR/com.dailyjobtool.$name.plist"
done
echo "done. logs kept in $LOG_DIR"
