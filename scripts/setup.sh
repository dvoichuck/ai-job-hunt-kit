#!/usr/bin/env bash
# Create local facts files from the examples if they are missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

copy_if_missing() {
  local src="$1"
  local dest="$2"
  if [ -f "$dest" ]; then
    echo "exists: ${dest#"$ROOT/"}"
  else
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    echo "created: ${dest#"$ROOT/"}"
  fi
}

copy_if_missing "$ROOT/local/profile.env.example" "$ROOT/local/profile.env"
copy_if_missing \
  "$ROOT/.cursor/skills/rewrite-cv/experience.example.md" \
  "$ROOT/.cursor/skills/rewrite-cv/experience.md"
copy_if_missing "$ROOT/.cursor/mcp.json.example" "$ROOT/.cursor/mcp.json"
copy_if_missing "$ROOT/linkedin-mcp/pack.example.json" "$ROOT/local/linkedin.pack.json"
copy_if_missing "$ROOT/boards/accounts.example.md" "$ROOT/boards/accounts.md"

echo "Fill local/profile.env and experience.md. Do not commit those copies."
echo "Scratch the working queue in NEXT.md."
