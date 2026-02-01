#!/usr/bin/env bash
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found. Install from https://cli.github.com/"
  exit 1
fi

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <owner/repo> <pr_number> <reviewer_login>"
  echo "Example: $0 bug39/blocks-demo 1 frogdog1"
  exit 1
fi

REPO="$1"
PR="$2"
REVIEWER="$3"

echo "Removing reviewer ${REVIEWER} from PR #${PR} in ${REPO}..."
gh api -X DELETE "repos/${REPO}/pulls/${PR}/requested_reviewers" -f "reviewers[]=${REVIEWER}"

echo "Current review requests:"
gh pr view "${PR}" --repo "${REPO}" --json reviewRequests --jq '.reviewRequests'
