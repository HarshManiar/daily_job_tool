#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
exec uv run daily_job_tool backup "$@"
