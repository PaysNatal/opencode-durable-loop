#!/usr/bin/env bash
# Apply the opencode reformatting fix to the globally-installed zeroshot package.
#
# Background: zeroshot's `reformatOutput` (the fallback that converts a non-JSON
# agent output into the required schema JSON) was shipped as a stub that always
# throws "SDK not implemented". This breaks the opencode planner, which spends its
# turn on tool-calls instead of emitting the final JSON plan block. The patch
# implements `reformatOutput` using the opencode CLI as the reformatting backend.
#
# Usage: ./scripts/apply-zeroshot-patch.sh [path-to-zeroshot-package]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$SCRIPT_DIR/../patches/zeroshot-opencode-reformat.patch"

# Locate the zeroshot package (arg, or global npm root).
PKG="${1:-$(npm root -g)/@the-open-engine/zeroshot}"
TARGET="$PKG/src/agent/output-reformatter.js"

if [[ ! -f "$TARGET" ]]; then
  echo "✗ zeroshot not found at: $PKG" >&2
  echo "  Install it first: npm install -g @the-open-engine/zeroshot" >&2
  exit 1
fi

# Backup the original exactly once.
[[ -f "$TARGET.bak" ]] || cp "$TARGET" "$TARGET.bak"

cd "$PKG"
if patch -p1 --forward --dry-run < "$PATCH" >/dev/null 2>&1; then
  patch -p1 --forward < "$PATCH"
  echo "✓ Patch applied: $TARGET"
elif patch -p1 --reverse --dry-run < "$PATCH" >/dev/null 2>&1; then
  echo "✓ Patch already applied — nothing to do"
else
  echo "✗ Patch does not apply cleanly — your zeroshot version may differ." >&2
  echo "  Target file: $TARGET" >&2
  echo "  Apply manually or regenerate the patch." >&2
  exit 1
fi
