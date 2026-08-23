#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SCRIPT="$ROOT/docs/video/dtprotocol_explainer.py"
VENV=${MANIMGL_VENV:-"$ROOT/.venv-manimgl"}
MANIM="$VENV/bin/manimgl"
OUT=${1:-"$ROOT/build/video"}
RAW="$OUT/raw"
FINAL="$OUT/DTProtocol-v4-architecture.mp4"
PACE=${DTP_VIDEO_PACE:-2.70}
REUSE_RAW=${DTP_VIDEO_REUSE_RAW:-0}
REVISION=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || printf unknown)

SCENES=(
  Opening
  Architecture
  Crystallization
  Feasibility
  EarlyData
  Reliability
  MultipartCompression
  Scheduler
  Repair
  Validation
  Closing
)

if [[ ! -x "$MANIM" ]]; then
  cat >&2 <<EOF
ManimGL was not found at:
  $MANIM

Create the environment with:
  python -m venv "$VENV"
  "$VENV/bin/pip" install -r "$ROOT/docs/video/requirements.txt"
EOF
  exit 1
fi

command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
command -v xvfb-run >/dev/null

if [[ "$REUSE_RAW" == 1 ]]; then
  mkdir -p "$RAW"
  for scene in "${SCENES[@]}"; do
    test -s "$RAW/$scene.mp4" || {
      echo "Missing reusable scene: $RAW/$scene.mp4" >&2
      exit 1
    }
  done
  rm -f "$OUT/without-chapters.mp4" "$OUT/chapters.ffmeta" "$FINAL"
else
  rm -rf "$OUT"
  mkdir -p "$RAW"
  for scene in "${SCENES[@]}"; do
    echo "Rendering $scene"
    xvfb-run -a "$MANIM" \
      "$SCRIPT" "$scene" -w --hd --video_dir "$RAW" --quiet
  done
fi

concat="$OUT/concat.txt"
: >"$concat"
for scene in "${SCENES[@]}"; do
  printf "file '%s/%s.mp4'\n" "$RAW" "$scene" >>"$concat"
done

# Render one compatible H.264 file, deliberately paced more slowly than the
# interactive scene defaults. The source scenes stay concise; the assembly step
# provides enough reading time without duplicating captions or filler text.
ffmpeg -y -v warning \
  -f concat -safe 0 -i "$concat" \
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
  -filter_complex "[0:v]setpts=${PACE}*PTS,fps=30,format=yuv420p[v]" \
  -map "[v]" -map 1:a \
  -c:v libx264 -preset slow -crf 18 \
  -c:a aac -b:a 96k -shortest -movflags +faststart \
  "$OUT/without-chapters.mp4"

# Add seekable chapter markers derived from the exact rendered scene durations.
python3 "$ROOT/docs/video/write_chapters.py" \
  --pace "$PACE" --raw "$RAW" --output "$OUT/chapters.ffmeta" \
  "${SCENES[@]}"

ffmpeg -y -v warning \
  -i "$OUT/without-chapters.mp4" \
  -i "$OUT/chapters.ffmeta" \
  -map 0 -map_metadata 1 \
  -metadata title="DTProtocol v4 architecture" \
  -metadata artist="DTProtocol project" \
  -metadata comment="Rendered from repository revision $REVISION" \
  -c copy -movflags +faststart "$FINAL"
rm "$OUT/without-chapters.mp4"

ffprobe -v error \
  -show_entries format=duration,size:stream=width,height,r_frame_rate,codec_name \
  -of default=nw=1 "$FINAL"
printf '\nCreated %s\n' "$FINAL"
