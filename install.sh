#!/bin/sh
# Install or update the paged-context Claude Code skill.
# Usage:  curl -fsSL https://raw.githubusercontent.com/CocoaBeans/paged-context-skill/main/install.sh | sh
set -eu

REPO="https://github.com/CocoaBeans/paged-context-skill.git"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/paged-context"

if [ -d "$DEST/.git" ]; then
  echo "Updating existing paged-context install at $DEST ..."
  git -C "$DEST" pull --ff-only
else
  echo "Installing paged-context skill to $DEST ..."
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 "$REPO" "$DEST"
fi

echo "Done. Reload Claude Code to pick up the skill."
