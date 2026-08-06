#!/usr/bin/env bash
# Regenerate every terminal image in docs/images from a real run of strata.
# Usage: scripts/make_docs_images.sh /path/to/a/repo
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-.}"
mkdir -p docs/images

render() { python3 scripts/ansi_to_svg.py "docs/images/$1.svg" --title "$2"; }

COLUMNS=96 python3 -m strata dig "$TARGET" --no-html --top 8 \
  | head -34 | render dig "strata dig"
COLUMNS=96 python3 -m strata coupling "$TARGET" --hidden --top 4 \
  | render coupling "strata coupling --hidden"

echo "wrote docs/images/{dig,coupling}.svg"
