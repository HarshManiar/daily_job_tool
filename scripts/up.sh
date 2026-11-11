#!/usr/bin/env bash
# Run the web UI and the crawl worker together; Ctrl-C stops both.
set -u
cd "$(dirname "$0")/.."
pids=()
cleanup() {
  trap - INT TERM EXIT
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
}
trap cleanup INT TERM EXIT
uv run firsthand web --port "${PORT:-8000}" & pids+=($!)
uv run firsthand worker & pids+=($!)
echo "web: http://127.0.0.1:${PORT:-8000}  (Ctrl-C stops web + worker)"
wait
