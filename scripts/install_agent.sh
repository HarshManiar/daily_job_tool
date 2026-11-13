#!/usr/bin/env bash
# Install launchd agents (web, worker, nightly backup) for this project. Use --dry-run first.
set -eu
. "$(dirname "$0")/_agents.sh"
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  [ "$DRY" = 1 ] && UV="/opt/homebrew/bin/uv" || { echo "uv not found on PATH" >&2; exit 1; }
fi
echo "project: $PROJECT_DIR"
echo "uv:      $UV"
if command -v pg_isready >/dev/null 2>&1 && pg_isready -q; then
  echo "postgres: running"
else
  echo "postgres: NOT reachable. Start it with: brew services start postgresql@17"
  echo "          (Homebrew services launches it at login; this script does not start it.)"
fi
[ -f "$PROJECT_DIR/.env" ] || echo "note: no .env in $PROJECT_DIR (set DATABASE_URL and BOT_CONTACT there)"
grep -q '^BOT_CONTACT=.\+' "$PROJECT_DIR/.env" 2>/dev/null || echo "warning: BOT_CONTACT is not set in .env"
run mkdir -p "$LA_DIR" "$LOG_DIR"
for name in $AGENTS; do
  src="$PROJECT_DIR/launchd/com.dailyjobtool.$name.plist"
  dst="$LA_DIR/com.dailyjobtool.$name.plist"
  gen() { sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__HOME__|$HOME|g" -e "s|__UV__|$UV|g" "$src"; }
  if [ "$DRY" = 1 ]; then
    echo "[dry-run] write $dst:"
    gen | sed 's/^/    | /'
  else
    gen > "$dst"
    plutil -lint "$dst" >/dev/null
  fi
  run launchctl bootout "gui/$(id -u)/com.dailyjobtool.$name" 2>/dev/null || true
  run launchctl bootstrap "gui/$(id -u)" "$dst"
done
echo "done. check with: make status   logs: $LOG_DIR"
