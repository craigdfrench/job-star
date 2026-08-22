#!/usr/bin/env bash
# Clean up superseded PRs for job-star repo.
# Closes PRs that are obsoleted by PR #24 or other merged work,
# leaving a clear comment linking to the replacement.
set -euo pipefail

REPO="craigdfrench/job-star"
REPLACEMENT_PR="${1:-24}"

# PRs to close (space-separated list identified as superseded)
SUPERSEDED_PRS="${SUPERSEDED_PRS:-}"

if [[ -z "$SUPERSEDED_PRS" ]]; then
  echo "No superseded PRs list provided via SUPERSEDED_PRS env var."
  echo "Usage: SUPERSEDED_PRS='12 15 18' $0"
  exit 0
fi

for pr in $SUPERSEDED_PRS; do
  echo "Closing PR #$pr as superseded by #$REPLACEMENT_PR..."
  gh pr comment "$pr" --repo "$REPO" --body "Closing as superseded by #$REPLACEMENT_PR which consolidates these changes. Please review the replacement PR." || {
    echo "Warning: could not comment on PR #$pr" >&2
  }
  gh pr close "$pr" --repo "$REPO" --comment "Superseded by #$REPLACEMENT_PR." || {
    echo "Warning: could not close PR #$pr" >&2
  }
done

echo "Cleanup complete."