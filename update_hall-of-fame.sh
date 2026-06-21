#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="$ROOT/dist/hof"
TMPROOT="$ROOT/dist/tmp"
mkdir -p "$TMPROOT"
BUILD="$(mktemp -d "$TMPROOT/hof-build.XXXXXX")"
cleanup() {
  if [ -n "${BUILD:-}" ] && [ -d "$BUILD" ]; then
    rm -rf "$BUILD"
  fi
  rmdir "$TMPROOT" 2>/dev/null || true
}
trap cleanup EXIT

chmod +x "$ROOT"/scripts/kvrosc_*.py
chmod +x "$ROOT"/scripts/scorecard_vote_extractor.py
chmod +x "$ROOT"/scripts/kvrosc_hall_of_fame_generator.py

# Fast default run:
# - loads the local archive cache
# - does not prefetch missing archive metadata
# - does not live-check archive download URLs
python "$ROOT/scripts/scorecard_vote_extractor.py" \
  --scorecards "$ROOT/scorecards" \
  --out "$BUILD"

# Optional slower modes, if you want stricter archive verification:
# python "$ROOT/scripts/scorecard_vote_extractor.py" \
#   --scorecards "$ROOT/scorecards" \
#   --out "$BUILD" \
#   --archive-prefetch-missing \
#   --verify-archive-links

python "$ROOT/scripts/kvrosc_hall_of_fame_generator.py" \
  --input "$BUILD" \
  --challenges "$ROOT/kvrosc_challenges.csv" \
  --config "$ROOT/scripts/kvrosc_hall_of_fame_config.yaml" \
  --out "$BUILD/index.html"

rm -rf "$DIST"
rm -rf "$ROOT/dist/hall-of-fame"
mkdir -p "$(dirname "$DIST")"
mkdir -p "$DIST"
required_files=(
  index.html \
  normalized_results.csv
)
for file in "${required_files[@]}"; do
  if [ ! -f "$BUILD/$file" ]; then
    echo "Missing expected build file: $file" >&2
    exit 1
  fi
done

for file in index.html normalized_results.csv .htaccess .passwd; do
  [ -f "$BUILD/$file" ] && cp "$BUILD/$file" "$DIST/"
done

if [ -f "$DIST/.htaccess" ] && [ -f "$DIST/.passwd" ]; then
  python - "$DIST" <<'PY'
from pathlib import Path
import sys

dist = Path(sys.argv[1])
htaccess = dist / ".htaccess"
passwd = dist / ".passwd"
lines = []
for line in htaccess.read_text(encoding="utf-8").splitlines():
    if line.startswith("AuthUserFile "):
        lines.append(f"AuthUserFile {passwd}")
    else:
        lines.append(line)
htaccess.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi
