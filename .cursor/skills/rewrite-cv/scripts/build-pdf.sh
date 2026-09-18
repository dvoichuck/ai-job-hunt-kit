#!/usr/bin/env bash
# Convert an HTML CV to a selectable-text PDF via headless Google Chrome.
# Usage: build-pdf.sh <input.html> [output.pdf]
set -euo pipefail

IN="${1:?usage: build-pdf.sh <input.html> [output.pdf]}"
OUT="${2:-${IN%.html}.pdf}"

CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"

"$CHROME" --headless=new --no-sandbox --disable-gpu \
  --no-pdf-header-footer \
  --print-to-pdf="$OUT" \
  "file://$(realpath "$IN")" 2>/dev/null

echo "Wrote $OUT"
