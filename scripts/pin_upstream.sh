#!/usr/bin/env bash
# Bump the pinned upstream SHA after you've reviewed upstream changes
# and updated the affected chapters' frontmatter.

set -euo pipefail

UPSTREAM="${LEANSPEC_REPO:-$HOME/Documents/leanSpec}"

if [[ ! -d "$UPSTREAM/.git" ]]; then
    echo "Upstream repo not found at $UPSTREAM" >&2
    echo "Set LEANSPEC_REPO=/path/to/leanSpec" >&2
    exit 1
fi

NEW_SHA=$(git -C "$UPSTREAM" rev-parse HEAD)
OLD_SHA=$(cat .upstream-sha 2>/dev/null || echo "<none>")

echo "Old pin: $OLD_SHA"
echo "New pin: $NEW_SHA"

if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
    echo "Already up to date."
    exit 0
fi

read -r -p "Bump pin? [y/N] " yn
if [[ "$yn" != "y" && "$yn" != "Y" ]]; then
    echo "Aborted."
    exit 1
fi

echo "$NEW_SHA" > .upstream-sha
echo "Pinned to $NEW_SHA"
