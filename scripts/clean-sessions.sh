#!/usr/bin/env bash
# Clean up opencode sessions left behind by zeroshot loop clusters.
#
# Each loop cluster runs opencode (conductor/planner/worker/validators) inside a
# per-cluster git worktree at ~/.zeroshot/worktrees/<cluster-id>. Those opencode
# sessions stay in opencode.db after the cluster finishes → garbage. This script
# deletes them.
#
# A session is deleted when its directory is a zeroshot worktree path AND that
# worktree directory no longer exists (the cluster finished and was torn down).
# Use --all to delete worktree sessions even if the worktree dir still exists
# (only do this when no cluster is currently running).
#
# Usage:
#   clean-sessions.sh              # delete orphaned worktree sessions (safe)
#   clean-sessions.sh --dry-run    # preview only
#   clean-sessions.sh --all        # delete ALL worktree sessions (no running clusters!)
set -euo pipefail

MODE="${1:-}"

DB="$(opencode db path 2>/dev/null | tail -1 || true)"
[[ -z "${DB:-}" || ! -f "$DB" ]] && DB="$HOME/.local/share/opencode/opencode.db"
[[ -f "$DB" ]] || { echo "✗ opencode db not found: $DB" >&2; exit 1; }

echo "▸ opencode db: $DB"

deleted=0
skipped=0
while IFS='|' read -r sid dir; do
  [[ -z "$sid" ]] && continue
  if [[ "$MODE" == "--all" || ! -d "$dir" ]]; then
    if [[ "$MODE" == "--dry-run" ]]; then
      echo "  would delete: ${sid:0:18}  (${dir##*/worktrees/})"
      deleted=$((deleted + 1))
    else
      if opencode session delete "$sid" >/dev/null 2>&1; then
        deleted=$((deleted + 1))
      else
        skipped=$((skipped + 1))
      fi
    fi
  else
    skipped=$((skipped + 1))
  fi
done < <(sqlite3 "$DB" "SELECT id || '|' || directory FROM session WHERE directory LIKE '%/.zeroshot/worktrees/%';")

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
