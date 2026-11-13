#!/usr/bin/env bash
# usage: scripts/restore.sh DUMP NEW_DB_NAME
set -eu
cd "$(dirname "$0")/.."
[ $# -eq 2 ] || { echo "usage: $0 DUMP NEW_DB_NAME" >&2; exit 2; }
exec uv run daily_job_tool restore "$1" --into "$2"
