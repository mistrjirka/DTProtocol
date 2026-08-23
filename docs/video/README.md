# DTProtocol ManimGL explainer

This directory contains the source for the animated DTProtocol v4 architecture
video. It uses **ManimGL**, the 3Blue1Brown branch of Manim.

The video is intentionally visual rather than a sequence of presentation slides:
route candidates, snapshots, packets, retransmissions, queue classes and link
failures move on screen. Captions stay short and name only the invariant being
shown.

- [Rendered 1080p MP4](releases/DTProtocol-v4-architecture.mp4)
- [Poster image](releases/DTProtocol-v4-architecture-poster.jpg)
- [SHA-256 checksums](releases/SHA256SUMS)

## Contents

- `dtprotocol_explainer.py` — eleven self-contained scenes;
- `render.sh` — clean 1080p render, final pacing, silent audio track and chapters;
- `write_chapters.py` — derives chapter timestamps from rendered scene lengths;
- `requirements.txt` — pinned Python packages;
- `NARRATION.md` — optional human voice-over script matched to the visuals.

## Arch Linux setup

```bash
sudo pacman -S --needed \
  python ffmpeg pango cairo mesa libglvnd xorg-server-xvfb base-devel

python -m venv .venv-manimgl
.venv-manimgl/bin/pip install -U pip wheel
.venv-manimgl/bin/pip install -r docs/video/requirements.txt
```

The scenes use Pango `Text` objects rather than LaTeX, so TeX is not required.

## Render

From the repository root:

```bash
MANIMGL_VENV="$PWD/.venv-manimgl" \
  docs/video/render.sh
```

The result is:

```text
build/video/DTProtocol-v4-architecture.mp4
```

Default output properties:

- 1920×1080;
- 30 fps;
- H.264 / yuv420p;
- silent stereo AAC track for broad player compatibility;
- seekable MP4 chapters;
- deliberately paced for the narration (`DTP_VIDEO_PACE=2.70`, about 4¾ minutes).

The pacing can be changed without editing scene code:

```bash
DTP_VIDEO_PACE=2.25 docs/video/render.sh
```


Rendered scene files can be reused when changing only the final pacing or MP4
metadata:

```bash
DTP_VIDEO_REUSE_RAW=1 DTP_VIDEO_PACE=2.70 \
  docs/video/render.sh build/video
```

## Git LFS

The rendered MP4 in `docs/video/releases/` is tracked with Git LFS. After a
fresh clone:

```bash
git lfs install
git lfs pull --include="docs/video/releases/*.mp4"
```

The Python source, render scripts, poster image, and narration remain normal Git
objects.

## Scene order

1. Why this is not application-level flooding.
2. The five cooperating DTPK state machines and the LCMM/MAC layers.
3. HELLO → CRYST_REQ → transactional multi-chunk CRYST.
4. Destination generations and the feasibility condition.
5. DATA while unrelated crystallization is incomplete.
6. Per-hop LCMM ACK versus end-to-end DTPK ACK and replay handling.
7. Airtime-aware compression and selective multipart repair.
8. Response/repair/normal scheduler classes and progress guarantees.
9. Cut/heal repair, requester-owned backoff and every-fourth-wave flooding.
10. The measured regression matrices.
11. The protocol in one sentence.

## Design rules used in the video

- no fabricated telemetry or decorative pseudo-data;
- no stock footage or unrelated imagery;
- no repeated conclusion in every section;
- no walls of prose while an animation is moving;
- no claim of a proof where the repository contains only bounded empirical tests;
- exact terminology and measured numbers from the current implementation.
