#!/usr/bin/env bash
# Clean up opencode sessions left behind by zeroshot loop clusters.
#
# Loop clusters create opencode sessions for each agent (conductor, planner,
# worker, validators) and for reformat calls. In zeroshot < 6.12 these lived in
# ~/.zeroshot/worktrees/<cluster-id>; in >= 6.12 they live in the project
# directory directly.
#
# This script cleans three categories:
#   1. Legacy worktree sessions (directory like %.zeroshot/worktrees/%)
#   2. Reformat sessions (title like "Text-to-JSON%" — always garbage)
#   3. Conductor/validator sessions (title patterns — always loop-created)
#
# Worker/planner sessions in project directories are NOT auto-deleted because
# they can't be reliably distinguished from user sessions by title alone.
# Use consolidate-sessions.py for manual cleanup of those.
#
# Usage:
#   clean-sessions.sh              # delete matching sessions
#   clean-sessions.sh --dry-run    # preview only
set -euo pipefail

MODE="${1:-}"

DB="$(opencode db path 2>/dev/null | tail -1 || true)"
[[ -z "${DB:-}" || ! -f "$DB" ]] && DB="$HOME/.local/share/opencode/opencode.db"
[[ -f "$DB" ]] || { echo "✗ opencode db not found: $DB" >&2; exit 1; }

echo "▸ opencode db: $DB"

# SQL: match worktree sessions OR reformat sessions OR conductor/validator sessions
QUERY="
  SELECT id || '|' || COALESCE(title,'') || '|' || COALESCE(directory,'')
  FROM session
  WHERE directory LIKE '%/.zeroshot/worktrees/%'
     OR title LIKE 'Text-to-JSON%'
     OR title LIKE 'Text to JSON%'
     OR title LIKE 'Junior conductor%'
     OR title LIKE '%Validator Prompt'
     OR title LIKE '%Validation Prompt'
     OR title LIKE '%conductor task classification%'
     OR title LIKE '%conductor classification prompt%'
;"

deleted=0
skipped=0
while IFS='|' read -r sid title dir; do
  [[ -z "$sid" ]] && continue
  # For worktree sessions, only delete if the worktree dir is gone (orphaned).
  # For title-matched sessions, always delete (they're unambiguously loop garbage).
  if [[ "$dir" == *".zeroshot/worktrees/"* && -d "$dir" && "$MODE" != "--all" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  if [[ "$MODE" == "--dry-run" ]]; then
    echo "  would delete: ${sid:0:18}  ${title:0:50}"
    deleted=$((deleted + 1))
  else
    if opencode session delete "$sid" >/dev/null 2>&1; then
      deleted=$((deleted + 1))
    else
      skipped=$((skipped + 1))
    fi
  fi
done < <(sqlite3 "$DB" "$QUERY")

if [[ "$MODE" == "--dry-run" ]]; then
  echo "▸ Dry run: $deleted session(s) would be deleted, $skipped kept."
else
  if [[ "$deleted" -gt 0 ]]; then
    echo "▸ Deleted $deleted loop session(s) ($skipped kept). Reclaiming space..."
    sqlite3 "$DB" "VACUUM;" 2>/dev/null || true
  else
    echo "▸ Nothing to delete ($skipped kept)."
  fi
  echo "✓ opencode.db size now: $(du -h "$DB" | awk '{print $1}')"
fi
