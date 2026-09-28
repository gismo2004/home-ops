#!/usr/bin/env python3
"""SABnzbd post-processing: make sure a finished movie/episode plays on the
CoreELEC box (Amlogic S905X4) before radarr/sonarr import it.

The one case this handles is broken Dolby Vision metadata: a single-layer
file (profile 8/10, el_present_flag=0) whose RPU still says "add an
enhancement-layer residual" (disable_residual_flag=0), i.e. a profile 7 RPU
copied in unconverted. The Amlogic DV core rejects it on every frame
(`AMDV ERROR: control_path failed`) and the screen stays black.

- broken DV with an HDR10/SDR/HLG base layer: strip the DV metadata without
  re-encoding (ffmpeg dovi_rpu bsf) -> plays as that base layer.
- broken DV without a usable base layer: exit 1, so the job fails and
  radarr/sonarr blocklist the release and grab another one
  (needs SABnzbd's special setting script_can_fail=1).
- anything else: untouched.

Arguments follow SABnzbd's script interface: argv[1] is the job's final
folder, argv[5] its category, argv[7] the post-processing status.
"""

import json
import os
import subprocess
import sys

# Static ffmpeg, mounted from an image volume (sabnzbd helmrelease).
FFMPEG_DIR = os.environ.get("MEDIA_CHECK_FFMPEG_DIR", "/opt/ffmpeg")
FFMPEG = os.path.join(FFMPEG_DIR, "ffmpeg")
FFPROBE = os.path.join(FFMPEG_DIR, "ffprobe")
CATEGORIES = {"movies", "tv"}
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".ts", ".m2ts"}
# Ignore samples and extras; SABnzbd already drops most samples.
MIN_SIZE = int(os.environ.get("MEDIA_CHECK_MIN_SIZE", 100 * 1024 * 1024))
# dv_bl_signal_compatibility_id values whose base layer plays without DV:
# 1 = HDR10, 2 = SDR, 4 = HLG. 0 (profile 5, 10.0) has no fallback.
FALLBACK_BL = {1: "HDR10", 2: "SDR", 4: "HLG"}


def probe(args):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-of", "json", *args],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def dovi_state(path):
    """Return None if the file has no single-layer DV, else
    (broken: bool, bl_compat_id: int, profile: int)."""
    streams = probe(["-select_streams", "v:0", "-show_streams", path])["streams"]
    if not streams:
        return None
    cfg = next(
        (s for s in streams[0].get("side_data_list", [])
         if s.get("side_data_type") == "DOVI configuration record"),
        None,
    )
    if cfg is None or cfg.get("el_present_flag") != 0:
        return None  # no DV, or a real dual-layer (profile 7) file
    frames = probe([
        "-select_streams", "v:0", "-read_intervals", "%+#1",
        "-show_frames", "-show_entries", "frame_side_data", path,
    ])["frames"]
    rpu = next(
        (s for f in frames for s in f.get("side_data_list", [])
         if s.get("side_data_type") == "Dolby Vision Metadata"),
        None,
    )
    broken = rpu is not None and rpu.get("disable_residual_flag") == 0
    return broken, cfg.get("dv_bl_signal_compatibility_id"), cfg.get("dv_profile")


def strip_dovi(path):
    root, ext = os.path.splitext(path)
    tmp = f"{root}.dvstrip{ext}"
    subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", path,
         "-map", "0", "-c", "copy", "-bsf:v:0", "dovi_rpu=strip=1", tmp],
        check=True,
    )
    # Sanity check before replacing: same duration, no DV left.
    old = float(probe(["-show_entries", "format=duration", path])["format"]["duration"])
    new = float(probe(["-show_entries", "format=duration", tmp])["format"]["duration"])
    if abs(old - new) > 1 or dovi_state(tmp) is not None:
        os.remove(tmp)
        raise RuntimeError(f"stripped file failed verification ({old:.0f}s vs {new:.0f}s)")
    os.replace(tmp, path)


def main():
    if len(sys.argv) < 8:
        print("usage: media_check.py <dir> <nzb> <name> <x> <category> <group> <status>")
        return 1
    job_dir, category, status = sys.argv[1], sys.argv[5], sys.argv[7]
    if status != "0" or category not in CATEGORIES:
        print(f"skipped (category={category}, status={status})")
        return 0

    videos = [
        os.path.join(d, f)
        for d, _, files in os.walk(job_dir) for f in files
        if os.path.splitext(f)[1].lower() in VIDEO_EXT
        and os.path.getsize(os.path.join(d, f)) >= MIN_SIZE
    ]
    for path in videos:
        name = os.path.basename(path)
        state = dovi_state(path)
        if state is None or not state[0]:
            continue
        _, bl, profile = state
        if bl not in FALLBACK_BL:
            print(f"REJECT {name}: broken DV profile {profile} RPU, no playable base layer")
            return 1
        strip_dovi(path)
        print(f"FIXED {name}: broken DV profile {profile} RPU stripped, plays as {FALLBACK_BL[bl]}")
    print(f"OK: {len(videos)} video file(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
