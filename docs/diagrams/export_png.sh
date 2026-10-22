#!/usr/bin/env bash
# Export each SVG in this folder to a PNG at 2x, using the headless browser.
#   ./export_png.sh [file.svg ...]      (default: every *.svg here)
# The SVG is the source of truth; re-run this after editing one.
# file:// and localhost are blocked for the browser here, so the SVG is passed
# inline as a data: URL. Needs playwright-cli and ~/.local/browser-libs (fonts, libs).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
. "$HOME/.local/browser-libs/env.sh"
OUT_DIR="${PLAYWRIGHT_MCP_OUTPUT_DIR:?playwright-cli output dir not set}"
[[ $# -gt 0 ]] || set -- *.svg
# open the headless browser if this shell's session has none yet
if playwright-cli goto about:blank 2>&1 | grep -q 'is not open'; then
  playwright-cli open about:blank >/dev/null
fi

for svg in "$@"; do
  read -r w h < <(python3 - "$svg" <<'PY'
import re, sys
head = open(sys.argv[1], encoding="utf-8").read(400)
w = re.search(r'width="(\d+)"', head).group(1)
h = re.search(r'height="(\d+)"', head).group(1)
print(int(w) * 2, int(h) * 2)
PY
)
  b64="$(base64 -w0 "$svg")"
  playwright-cli goto "data:text/html;charset=utf-8,<html><body style='margin:0;background:%23fff'><img style='display:block;width:${w}px' src='data:image/svg+xml;base64,${b64}'></body></html>" >/dev/null
  playwright-cli resize "$w" "$h" >/dev/null
  shot="$(playwright-cli screenshot | grep -oE 'page-[0-9TZ:.-]+\.png' | head -1)"
  [[ -n "$shot" ]] || { echo "screenshot failed for $svg" >&2; exit 1; }
  cp "$OUT_DIR/$shot" "${svg%.svg}.png"
  echo "${svg%.svg}.png (${w}x${h})"
done
