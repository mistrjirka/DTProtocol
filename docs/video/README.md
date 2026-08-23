# DTProtocol ManimGL explainer

This directory contains the source for the animated DTProtocol v4 architecture
video. It uses **ManimGL**, the 3Blue1Brown branch of Manim.

The video is intentionally visual rather than a sequence of presentation slides.
Crystallization is built in the same order a viewer should understand it: physical
reach, direct discovery, staged snapshot, atomic commit, then route use. Packets
move one hop at a time. Captions name the current invariant rather than replacing
the animation with prose.

- [Rendered 1080p MP4](releases/DTProtocol-v4-architecture.mp4)
- [Poster image](releases/DTProtocol-v4-architecture-poster.jpg)
- [SHA-256 checksums](releases/SHA256SUMS)

## Contents

- `dtprotocol_explainer.py` — eleven self-contained scenes;
- `render.sh` — clean 1080p render, final pacing, silent audio track and chapters;
- `write_chapters.py` — derives chapter timestamps from rendered scene lengths;
- `check_layout.py` — geometry/layer contracts for clipped links, packet clearance, and the caption band;
- `requirements.txt` — pinned Python packages;
- `STORYBOARD.md` — narrative/visual grammar and scene-level QC requirements;
- `make_qc_contact_sheets.sh` — dense sampled-frame sheets for visual transition review;
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

The results are:

```text
build/video/DTProtocol-v4-architecture.mp4
build/video/DTProtocol-v4-architecture-poster.jpg
```

Default output properties:

- 1920×1080;
- 30 fps;
- H.264 / yuv420p;
- silent stereo AAC track for broad player compatibility;
- seekable MP4 chapters;
- paced for a silent visual explainer (`DTP_VIDEO_PACE=1.45`); narration should be edited against the rendered clips rather than obtained by slowing every animation;
- poster extracted from a deliberate stable Opening frame (`DTP_VIDEO_POSTER_TIME=3.20`), never from a transition frame.

The pacing can be changed without editing scene code:

```bash
DTP_VIDEO_PACE=1.25 docs/video/render.sh
```


Rendered scene files can be reused when changing only the final pacing or MP4
metadata:

```bash
DTP_VIDEO_REUSE_RAW=1 DTP_VIDEO_PACE=1.45 \
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

1. Open on the question: can A send to C while unrelated mesh state is still crystallizing?
2. Build crystallization from scratch: no learned links, one physically correct radio-range circle at a time, direct discovery, staged CRYST chunks, then atomic route commit.
3. Send DATA A → B → C while a separate CRYST transaction remains pending, then return the E2E ACK hop by hop over the reverse breadcrumb.
4. Explain destination generations and the feasibility condition without animating a packet across a failed link.
5. Separate per-hop LCMM reliability from DTPK end-to-end completion and replay handling.
6. Show the measured airtime-aware compression case, then a three-fragment hop-by-hop transfer with selective repair of one missing fragment.
7. Break C–E, let directed repair reach the break, show the periodic flood over surviving links, return fresh state, and immediately use the repaired route.
8. Show response/repair/normal scheduler classes and progress guarantees.
9. Only then name the full architecture as a recap of mechanisms already seen.
10. Show the measured regression matrices and their limits.
11. End on local usefulness: committed knowledge works while replacement knowledge is still incomplete.

## Story and animation rules

The editorial order follows Grant Sanderson's public advice for mathematical
exposition: motivate early, put concrete examples before general frameworks,
do not begin with definitions, and make every screen movement communicate the
same point as the explanation. Manim renders individual visual clips; pacing,
chapters, and any voice-over are assembled afterward.

## Design rules used in the video

- no fabricated telemetry or decorative pseudo-data;
- no stock footage or unrelated imagery;
- no repeated conclusion in every section;
- no walls of prose while an animation is moving;
- no claim of a proof where the repository contains only bounded empirical tests;
- exact terminology and measured numbers from the current implementation.

## Editorial references

The storytelling/layout pass was checked against:

- Grant Sanderson's public advice for new math explainers:
  https://www.3blue1brown.com/about/
- Summer of Math Exposition judging criteria (clarity, motivation, novelty,
  memorability): https://www.3blue1brown.com/blog/some1/
- 3Blue1Brown's ManimGL workflow demo:
  https://www.3blue1brown.com/lessons/manim-demo/

The useful constraints for this video are concrete: motivate the question early,
show an example before naming the general machinery, do not animate text merely
because Manim can animate it, and keep narration/editing as a separate
post-production concern.

## Visual QC

A successful Manim render is not considered sufficient. Before publishing a new
release, generate dense 4-fps contact sheets from every scene and inspect transition
frames as well as stable states:

```bash
docs/video/make_qc_contact_sheets.sh build/video/raw build/video/qc-sheets
```

The source-level layout check also rejects the failure modes that caused the old
poster/video defects: center-to-center node links, semantic glyph morphing, and
packet paths whose boxes can intrude into node labels.
