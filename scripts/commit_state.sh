#!/usr/bin/env bash
# Commit state.json back to the repo after a successful send.
#
# Three things this has to get right, and they are the three the tests cover:
#   * a changed state.json is committed and pushed,
#   * nothing staged means exit 0, not a failed job over an empty commit,
#   * the branch can move between checkout and push, so a rejected push is
#     rebased onto whatever landed and retried rather than forced.
#
# Usage: scripts/commit_state.sh [state-file]
# Env:   BRANCH (default $GITHUB_REF_NAME, else main), ATTEMPTS, SLEEP_BASE

set -euo pipefail

STATE_FILE="${1:-state.json}"
BRANCH="${BRANCH:-${GITHUB_REF_NAME:-main}}"
ATTEMPTS="${ATTEMPTS:-5}"
SLEEP_BASE="${SLEEP_BASE:-2}"

git config user.name "${GIT_AUTHOR_NAME:-portfolio-monitor}"
git config user.email "${GIT_AUTHOR_EMAIL:-portfolio-monitor@users.noreply.github.com}"

if [ ! -f "$STATE_FILE" ]; then
  echo "commit_state: no $STATE_FILE to commit — nothing to do"
  exit 0
fi

git add -- "$STATE_FILE"

if git diff --cached --quiet -- "$STATE_FILE"; then
  echo "commit_state: $STATE_FILE is unchanged — nothing to commit"
  exit 0
fi

git commit -m "state: weekly run $(date -u +%Y-%m-%d)" -- "$STATE_FILE"

for attempt in $(seq 1 "$ATTEMPTS"); do
  if git push origin "HEAD:refs/heads/$BRANCH"; then
    echo "commit_state: pushed to $BRANCH on attempt $attempt"
    exit 0
  fi
  echo "commit_state: push rejected — $BRANCH moved. Rebasing (attempt $attempt/$ATTEMPTS)."
  if ! git pull --rebase origin "$BRANCH"; then
    echo "commit_state: rebase failed; leaving the branch alone rather than forcing." >&2
    exit 1
  fi
  sleep "$(( attempt * SLEEP_BASE ))"
done

echo "commit_state: could not push after $ATTEMPTS attempts." >&2
exit 1
