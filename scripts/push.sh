#!/bin/sh
# Push to GitHub using a token kept OUTSIDE the repo (~/.daily_job_tool/github_token).
# One-time setup: copy a token to the clipboard, then run:
#   mkdir -p ~/.daily_job_tool && pbpaste > ~/.daily_job_tool/github_token && chmod 600 ~/.daily_job_tool/github_token
# Usage: scripts/push.sh   (pushes the current branch to origin)
set -eu
f="$HOME/.daily_job_tool/github_token"
[ -s "$f" ] || { echo "No token at $f (see the header of this script)"; exit 1; }
ask="$(mktemp)"; trap 'rm -f "$ask"' EXIT
printf '#!/bin/sh\ncat "%s"\n' "$f" > "$ask"; chmod 700 "$ask"
GIT_ASKPASS="$ask" GIT_TERMINAL_PROMPT=0 git -c credential.helper= push -u origin "$(git branch --show-current)"
