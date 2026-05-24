#!/bin/bash
# BigCat Learning Hub — shared publish script for all routine repos.
# Auto-derives N, commit message, and URL from filesystem state.
# Usage: ./publish.sh   (no args)
#
# Guards:
#   - new content file exists with name pattern *-{day,week,book,issue}{N}.html
#   - content file is not a tiny error stub (>2KB)
#   - index.html references the new file
#   - TOPICS.md has an [x] entry for #N
#   - no hardcoded shared scripts (comments/search/index-button)
#   - pushes to main via HEAD:main (bypasses claude/* harness branches)
#   - retries push up to 3 times on transient failures

set -e

NEW=$(git status --porcelain | grep -oE '\S+-(day|week|book|issue)[0-9]+\.html$' | head -1)
[ -z "$NEW" ] && { echo "ERROR: no new *-{day,week,book,issue}{N}.html in working tree"; exit 1; }

N=$(echo "$NEW" | grep -oE '[0-9]+' | tail -1)

TITLE=$(grep -oE '<title>[^<]+' "$NEW" | head -1 | sed 's|<title>||')
[ -z "$TITLE" ] && TITLE="$NEW"
MSG="${MSG:-Add #$N: $TITLE}"

[ $(wc -c < "$NEW") -lt 2000 ] && { echo "ERROR: $NEW only $(wc -c < $NEW) bytes, likely incomplete"; exit 1; }

grep -q "$NEW" index.html || { echo "ERROR: index.html does not reference $NEW"; exit 1; }

grep -qE "\[x\].*(\b$N\b|#$N)" TOPICS.md || { echo "ERROR: TOPICS.md missing [x] entry for #$N"; exit 1; }

for f in comments.js search.js index-button.js; do
  grep -q "$f" "$NEW" && { echo "ERROR: $NEW hardcodes $f (auto-injected, will duplicate)"; exit 1; }
done
grep -q "← Hub" "$NEW" && echo "WARN: $NEW hardcodes ← Hub button (will be deduped, consider removing)"

git config user.email "chengchen0802@gmail.com"
git config user.name "BigCat"
git add -A
git diff --cached --quiet && { echo "Nothing to commit."; exit 0; }
git commit -m "$MSG"

for i in 1 2 3; do
  if git push origin HEAD:main 2>/dev/null; then
    echo "✓ Pushed: $MSG"
    REPO=$(basename "$(pwd)")
    echo "→ https://cissy0802.github.io/$REPO/$NEW"
    exit 0
  fi
  echo "Push attempt $i failed, fetching and rebasing..."
  git fetch origin main
  git rebase origin/main || { git rebase --abort; echo "Rebase failed"; }
  [ $i -lt 3 ] && sleep 2
done
echo "ERROR: push failed after 3 attempts"
exit 1
