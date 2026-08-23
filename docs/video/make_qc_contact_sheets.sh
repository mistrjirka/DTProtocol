#!/usr/bin/env bash
set -euo pipefail

# Generate dense visual-QC sheets from already-rendered per-scene MP4s.
# Two samples per second catches short-lived overlay/morph mistakes that a
# beginning/middle/end screenshot check routinely misses.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RAW=${1:-"$ROOT/build/video/raw"}
OUT=${2:-"$ROOT/build/video/qc-sheets"}
FPS=${DTP_QC_FPS:-2}

command -v ffmpeg >/dev/null
mkdir -p "$OUT"
rm -f "$OUT"/*.jpg

shopt -s nullglob
for video in "$RAW"/*.mp4; do
  scene=$(basename "$video" .mp4)
  echo "QC sheets: $scene"
  # 4x4 tiles at 480 px each -> 1920-wide sheets, 8 seconds per sheet at 2 fps.
  ffmpeg -nostdin -y -v warning \
    -i "$video" \
    -vf "fps=${FPS},scale=480:-2:flags=lanczos,tile=4x4:padding=4:margin=4" \
    -q:v 2 -vsync 0 "$OUT/${scene}-%03d.jpg"
done

printf '\nCreated contact sheets in %s\n' "$OUT"
