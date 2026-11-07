#!/usr/bin/env bash
# Retry non-fast-forward/ref-lock races when independent data workflows commit to main.
set -euo pipefail

branch="${1:-${GITHUB_REF_NAME:-main}}"
max_attempts="${MAX_GIT_PUSH_ATTEMPTS:-6}"

if [[ -z "$branch" ]]; then
  echo "A target branch is required." >&2
  exit 2
fi

if ! [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
  echo "MAX_GIT_PUSH_ATTEMPTS must be a positive integer." >&2
  exit 2
fi

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  echo "Git push attempt ${attempt}/${max_attempts} for ${branch}"

  if ! git fetch --no-tags origin "+refs/heads/${branch}:refs/remotes/origin/${branch}"; then
    echo "Fetch failed; will retry." >&2
  elif ! git rebase "refs/remotes/origin/${branch}"; then
    echo "Rebase conflict; refusing to force-push or discard data." >&2
    git rebase --abort || true
    exit 1
  elif git push origin "HEAD:refs/heads/${branch}"; then
    echo "Committed data changes pushed successfully."
    exit 0
  else
    echo "Remote branch moved during push; refreshing and retrying." >&2
  fi

  if (( attempt < max_attempts )); then
    sleep "$((attempt * 2))"
  fi
done

echo "Unable to push after ${max_attempts} attempts." >&2
exit 1
