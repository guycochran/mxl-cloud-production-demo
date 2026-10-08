#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# DCO check: every non-merge commit in BASE..HEAD must carry a
#   Signed-off-by: Name <email>
# trailer whose email matches the commit's author email (case-insensitive).
# See CONTRIBUTING.md ("Developer Certificate of Origin").
#
# Usage: scripts/check-dco.sh <base-ref> [<head-ref>]   (head defaults to HEAD)
# Exit 0 = all signed off; 1 = at least one commit is missing a matching sign-off;
# 2 = usage / git error. Dependency-free (bash + git).
set -euo pipefail

BASE="${1:-}"; HEAD_REF="${2:-HEAD}"
if [ -z "$BASE" ]; then
  echo "usage: $0 <base-ref> [<head-ref>]" >&2; exit 2
fi
git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null || { echo "unknown base ref: $BASE" >&2; exit 2; }
git rev-parse --verify --quiet "$HEAD_REF^{commit}" >/dev/null || { echo "unknown head ref: $HEAD_REF" >&2; exit 2; }

fail=0; checked=0
for c in $(git rev-list --no-merges "$BASE..$HEAD_REF"); do
  checked=$((checked + 1))
  author_email=$(git log -1 --format='%ae' "$c" | tr '[:upper:]' '[:lower:]')
  subject=$(git log -1 --format='%s' "$c")
  # all Signed-off-by trailers, emails only, lower-cased
  signoffs=$(git log -1 --format='%(trailers:key=Signed-off-by,valueonly)' "$c" \
             | sed -n 's/.*<\([^>]*\)>.*/\1/p' | tr '[:upper:]' '[:lower:]')
  if [ -z "$signoffs" ]; then
    echo "✗ ${c:0:10} missing Signed-off-by — \"$subject\""; fail=1
  elif ! printf '%s\n' "$signoffs" | grep -qxF "$author_email"; then
    echo "✗ ${c:0:10} Signed-off-by does not match author <$author_email> — \"$subject\""; fail=1
  else
    echo "✓ ${c:0:10} $subject"
  fi
done

if [ "$fail" -ne 0 ]; then
  cat <<'MSG'

DCO check failed. Sign off your commits (see CONTRIBUTING.md):
  git rebase --signoff <base>      # adds Signed-off-by to every commit on the branch
  git push --force-with-lease
MSG
  exit 1
fi
echo "DCO: $checked commit(s) checked, all signed off."
