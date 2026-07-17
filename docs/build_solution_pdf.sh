#!/usr/bin/env bash
# Rebuild docs/assignment_solution.pdf from assignment_solution.md.
#
#   markdown --(pandoc)--> standalone HTML --(headless Chrome)--> PDF
#
# The original PDF was produced this way but the recipe was never committed, so the
# styling couldn't be reproduced. It is committed now: edit the .md (and .css for
# layout), then run this script.
#
# Requires: pandoc, Google Chrome.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/assignment_solution.pdf"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

command -v pandoc >/dev/null || { echo "pandoc not found (brew install pandoc)" >&2; exit 1; }
[ -x "$CHROME" ] || { echo "Chrome not found at: $CHROME (set \$CHROME)" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# pandoc's default template inlines its own <style>; injecting ours into <head>
# afterwards lets it win without needing an external stylesheet at render time.
printf '<style>\n%s\n</style>\n' "$(cat "$DIR/assignment_solution.css")" > "$TMP/header.html"
pandoc "$DIR/assignment_solution.md" -s -H "$TMP/header.html" -o "$TMP/solution.html"

rm -f "$OUT"
# Chrome doesn't reliably exit after --print-to-pdf, so poll for the file and stop it.
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
  --no-first-run --no-default-browser-check --disable-extensions \
  --disable-background-networking --disable-component-update --disable-sync \
  --virtual-time-budget=5000 --user-data-dir="$TMP/chrome" \
  --print-to-pdf="$OUT" "file://$TMP/solution.html" >/dev/null 2>&1 &
PID=$!
for _ in $(seq 1 40); do [ -s "$OUT" ] && break; sleep 1; done
sleep 2
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

[ -s "$OUT" ] || { echo "PDF was not produced" >&2; exit 1; }
echo "built: $OUT"
command -v pdfinfo >/dev/null && pdfinfo "$OUT" | grep -E '^Pages' || true
