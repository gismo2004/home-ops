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
- no video at all (fake release) or executables inside (malware dressed
  up as a movie): exit 1, same blocklist-and-retry path. Scoped to the
  movies/tv categories, so software downloads keep their .exe/.iso files.
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
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".ts", ".m2ts", ".avi"}
# Never part of a real movie/episode release.
EXEC_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".lnk", ".ps1",
            ".vbs", ".js", ".jar"}
# "Is there a video at all" threshold; well below MIN_SIZE because short
# kids' episodes in x265 are only ~100 MB.
HAS_VIDEO_SIZE = 20 * 1024 * 1024
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


GAME_EXT = (".nsp", ".nsz", ".xci", ".xcz")


def switch_container_ext(path):
    """Return the extension a Switch container should have, read from its
    header, or None if it is not one. NSP/NSZ are PFS0 at offset 0 (an NSZ
    lists .ncz entries in its name table); XCI/XCZ carry "HEAD" at 0x100."""
    with open(path, "rb") as fh:
        head = fh.read(0x2000)
    if head[:4] == b"PFS0":
        return ".nsz" if b".ncz\0" in head else ".nsp"
    if head[0x100:0x104] == b"HEAD":
        return ".xci"
    return None


def unobfuscate_games(job_dir):
    """Scene groups hide the container behind a fake suffix: SUXXORS
    "x.nsp.hdf", HR "x.xci.json" or a bare "x.hdf". Ownfoil and gamarr only
    see .nsp/.nsz/.xci/.xcz, so give every Switch container the extension
    its header says, keeping an inner extension when it is already right."""
    renamed = 0
    for d, _, fs in os.walk(job_dir):
        for f in fs:
            old_p = os.path.join(d, f)
            if f.lower().endswith(GAME_EXT) or os.path.getsize(old_p) < 1024 * 1024:
                continue
            ext = switch_container_ext(old_p)
            if ext is None:
                continue
            stem = os.path.splitext(f)[0]
            inner = os.path.splitext(stem)[1].lower()
            new_name = stem if inner in GAME_EXT else stem + ext
            new_p = os.path.join(d, new_name)
            if os.path.exists(new_p):
                print(f"NOT renaming {f}: {new_name} already exists")
                continue
            os.replace(old_p, new_p)
            print(f"Renamed {f} -> {new_name}")
            renamed += 1
    return renamed


def main():
    if len(sys.argv) < 8:
        print("usage: media_check.py <dir> <nzb> <name> <x> <category> <group> <status>")
        return 1
    job_dir, category, status = sys.argv[1], sys.argv[5], sys.argv[7]
    if status != "0":
        print(f"skipped (failed job, status={status})")
        return 0

    if category == "games":
        renamed = unobfuscate_games(job_dir)
        print(f"Games post-processing complete ({renamed} file(s) renamed)")
        return 0

    if category not in CATEGORIES:
        print(f"skipped (category={category}, status={status})")
        return 0

    files = [os.path.join(d, f) for d, _, fs in os.walk(job_dir) for f in fs]
    execs = [f for f in files if os.path.splitext(f)[1].lower() in EXEC_EXT]
    if execs:
        print(f"REJECT executable file(s) in a media release: {os.path.basename(execs[0])}")
        return 1
    if not any(os.path.splitext(f)[1].lower() in VIDEO_EXT
               and os.path.getsize(f) >= HAS_VIDEO_SIZE for f in files):
        print("REJECT no video file in the release")
        return 1
    videos = [
        f for f in files
        if os.path.splitext(f)[1].lower() in VIDEO_EXT
        and os.path.getsize(f) >= MIN_SIZE
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
