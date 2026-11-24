#!/usr/bin/env bash
# Build the browser version and zip it:  $DIST/figcraft-web-<version>.zip
#
# Usage: packaging/web/package.sh [--skip-build]
#
# Needs: trunk (brew install trunk / cargo install trunk --locked) and the wasm32-unknown-unknown
# target. The zip holds a self-contained static site in figcraft-web-<version>/ that works
# from any URL path and inside an <iframe>. Hosting notes: packaging/web/README.md.
set -euo pipefail
# shellcheck source=../env.sh
. "$(dirname "${BASH_SOURCE[0]}")/../env.sh"
HERE="$ROOT/packaging/web"

if [[ "${1:-}" != "--skip-build" ]]; then
  command -v trunk >/dev/null || { echo "error: trunk not found (cargo install trunk --locked)" >&2; exit 1; }
  (cd "$ROOT/apps/figcraft-web" && trunk build --release)
fi

SITE="$ROOT/dist/web"
[[ -f "$SITE/index.html" ]] || { echo "error: $SITE/index.html missing; run without --skip-build" >&2; exit 1; }
# Paths must be relative so the site works under any prefix (public_url = "./" in Trunk.toml).
if grep -Eq '(src|href)="/[^/]' "$SITE/index.html"; then
  echo "error: $SITE/index.html has root-absolute URLs; it would break when served from a sub-path" >&2
  exit 1
fi

# Size gate: Cloudflare Pages/Workers reject any single file over 25 MiB, and other
# hosts and CDNs have similar caps. Oversized modules are split losslessly and reassembled by
# the browser loader; each emitted asset still obeys the cap.
MAX_WASM_BYTES="${FIGCRAFT_WASM_MAX_BYTES:-26214400}" # 25 MiB (Cloudflare per-file cap)
WASM_COUNT=0
for wasm in "$SITE"/*.wasm; do
  [[ -f "$wasm" ]] || continue
  WASM_COUNT=$((WASM_COUNT + 1))
  size=$(wc -c <"$wasm" | tr -d ' ')
  echo "$(basename "$wasm"): $size bytes ($((size / 1048576)) MiB; limit $MAX_WASM_BYTES)"
  if [[ "$size" -gt "$MAX_WASM_BYTES" ]]; then
    python3 "$HERE/split_wasm.py" "$wasm" "$MAX_WASM_BYTES"
  fi
done
[[ "$WASM_COUNT" -gt 0 ]] || { echo "error: no .wasm in $SITE" >&2; exit 1; }
for wasm in "$SITE"/*.wasm; do
  [[ $(wc -c <"$wasm") -le "$MAX_WASM_BYTES" ]] || { echo "error: oversized asset $wasm" >&2; exit 1; }
done

NAME="figcraft-web-$VERSION"
WORK="$CARGO_TARGET_DIR/web-package"
rm -rf "$WORK"
mkdir -p "$WORK/$NAME"
cp -R "$SITE/." "$WORK/$NAME/"
# Sample server configs (MIME type, caching, compression); harmless where unused.
cp "$HERE/_headers" "$HERE/.htaccess" "$WORK/$NAME/"
cp "$HERE/README.md" "$WORK/$NAME/HOSTING.md"
copy_docs "$WORK/$NAME"
rm -f "$DIST/$NAME.zip"
(cd "$WORK" && zip -qr9 "$DIST/$NAME.zip" "$NAME")
echo "wrote $DIST/$NAME.zip"
ls -lh "$DIST/$NAME.zip"
