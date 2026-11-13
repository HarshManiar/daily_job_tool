#!/usr/bin/env bash
# Fail if tracked files leak personal identifiers or the private profile.
# Usage: scripts/check_private.sh   (or: make check-private)
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

PATTERN='axtria|harshmaniar|BOT_CONTACT=.+@'
status=0

if git ls-files --error-unmatch data/profile.yaml >/dev/null 2>&1; then
  echo "FAIL: data/profile.yaml is tracked (it must stay gitignored)"
  status=1
fi

# This script contains the pattern itself, so exclude it from the scan.
hits=$(git ls-files -z -- . ':!:scripts/check_private.sh' \
  | xargs -0 grep -IEin -- "$PATTERN" 2>/dev/null || true)

if [ -n "$hits" ]; then
  echo "FAIL: private identifiers found in tracked files:"
  echo "$hits"
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "OK: no private identifiers in tracked files"
fi
exit "$status"
