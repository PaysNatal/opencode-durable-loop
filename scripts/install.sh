#!/usr/bin/env bash
# Install the opencode durable loop:
#   1. Check dependencies (temporal CLI, zeroshot, opencode, python3, jq, memorix).
#   2. Install Python deps (temporalio).
#   3. Apply the zeroshot opencode reformatting patch.
#   4. Link dloop + zs-learn into ~/.local/bin.
#   5. Set up the memorix memory vault (~/memory-vault git repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="$(dirname "$SCRIPT_DIR")"
BIN_DIR="${DLOOP_BIN_DIR:-$HOME/.local/bin}"
VAULT="${DLOOP_MEMORY_VAULT:-$HOME/memory-vault}"

echo "═══ opencode-durable-loop installer ═══"

# 1. Dependency checks ------------------------------------------------------
echo "▸ Checking dependencies..."
MISSING=0
need() {
  if command -v "$1" >/dev/null 2>&1; then echo "  ✓ $1";
  else echo "  ✗ $1 NOT FOUND — $2"; MISSING=1; fi
}
need temporal "brew install temporal"
need zeroshot "npm install -g @the-open-engine/zeroshot"
need opencode "see https://opencode.ai"
need python3  "install Python 3.10+"
need jq       "brew install jq"
need memorix  "npm install -g memorix  (optional: cross-project lesson memory)"
if [[ "$MISSING" == "1" ]]; then
  echo "Install the missing dependencies first, then re-run." >&2
  exit 1
fi

# 2. Python deps ------------------------------------------------------------
echo "▸ Installing Python dependencies (temporalio)..."
python3 -m pip install --quiet -r "$PROJ_DIR/requirements.txt"

# 3. zeroshot patch ---------------------------------------------------------
echo "▸ Applying zeroshot opencode reformatting patch..."
bash "$SCRIPT_DIR/apply-zeroshot-patch.sh"

# 4. Link executables -------------------------------------------------------
echo "▸ Linking dloop + zs-learn into $BIN_DIR..."
mkdir -p "$BIN_DIR"
chmod +x "$PROJ_DIR/dloop" "$PROJ_DIR/zs-learn"
ln -sf "$PROJ_DIR/dloop" "$BIN_DIR/dloop"
ln -sf "$PROJ_DIR/zs-learn" "$BIN_DIR/zs-learn"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  ⚠ $BIN_DIR is not in PATH — add: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# 5. memorix memory vault ---------------------------------------------------
if [[ -d "$VAULT/.git" ]]; then
  echo "  ✓ memory vault exists: $VAULT"
else
  echo "▸ Creating memorix memory vault at $VAULT..."
  mkdir -p "$VAULT"
  git -C "$VAULT" init -q
  echo "# Memorix memory vault for the durable loop" > "$VAULT/README.md"
  git -C "$VAULT" add README.md
  git -C "$VAULT" -c user.email=loop@local -c user.name=loop commit -qm "init memory vault" || true
  echo "  ✓ created $VAULT"
fi

echo ""
echo "✓ Installed. Try:  dloop \"your task\" --detach"
