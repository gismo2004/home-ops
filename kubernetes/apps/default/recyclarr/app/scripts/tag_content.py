#!/usr/bin/env python3
"""Nightly: make Radarr/Sonarr score existing files by their real content.

Custom formats only look at a file's stored release name (sceneName), and
many release groups leave the audio format and HDR out of it. A later
release that merely names "DTS" then looks like an upgrade over a TrueHD
file. This appends the audio format and HDR type from the arr's own media
analysis to the stored name, only when no audio/HDR format matches yet,
and never lets a file's score go down.

Files whose first audio track is not German are skipped and logged: the
arr reports only the first track's codec, which then says nothing about
the German audio. Files without a stored release name are skipped too
(their score comes from the file name; renaming files would make Jellyfin
create new items and lose hearts).

Env: RADARR_URL, RADARR_API_KEY, SONARR_URL, SONARR_API_KEY.
"""
import json
import os
import re
import sys
import time
import urllib.request

AUDIO_CF = {"TrueHD ATMOS", "DTS X", "TrueHD", "DTS-HD MA", "FLAC", "PCM", "DTS-HD HRA",
            "ATMOS (undefined)", "DD+ ATMOS", "DD+", "DTS-ES", "DTS", "AAC", "DD", "MP3", "Opus"}
HDR_CF = {"HDR", "DV Boost", "HDR10+ Boost", "DV", "DV HDR10", "DV HDR10Plus", "HDR10",
          "HDR10+", "DV (w/o HDR fallback)", "PQ", "HLG"}
AUDIO_TOKEN = {"TrueHD Atmos": "TrueHD.Atmos", "TrueHD": "TrueHD", "DTS-X": "DTS-X",
               "DTS-HD MA": "DTS-HD.MA", "DTS-HD HRA": "DTS-HD.HRA", "DTS-ES": "DTS-ES",
               "DTS": "DTS", "EAC3 Atmos": "DDP.Atmos", "EAC3": "DDP", "AC3": "DD",
               "AAC": "AAC", "FLAC": "FLAC", "PCM": "LPCM", "Opus": "Opus", "MP3": "MP3"}
HDR_TOKEN = {"DV HDR10": "DV.HDR10", "DV HDR10Plus": "DV.HDR10Plus", "DV HLG": "DV.HLG",
             "DV SDR": "DV", "DV": "DV", "HDR10": "HDR10", "HDR10Plus": "HDR10Plus",
             "HLG": "HLG", "PQ": "PQ"}


class Arr:
    def __init__(self, name, url, key):
        self.name, self.url, self.key = name, url.rstrip("/"), key
        self.ep = "moviefile" if name == "radarr" else "episodefile"

    def call(self, method, path, body=None, tries=4):
        for i in range(tries):
            try:
                req = urllib.request.Request(
                    self.url + path, method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    txt = r.read()
                    return json.loads(txt) if txt else None
            except Exception:
                if i == tries - 1:
                    raise
                time.sleep(2 * (i + 1))

    def files(self):
        if self.name == "radarr":
            for m in self.call("GET", "/api/v3/movie"):
                if m.get("hasFile"):
                    yield from self.call("GET", f"/api/v3/moviefile?movieId={m['id']}")
        else:
            for s in self.call("GET", "/api/v3/series"):
                yield from self.call("GET", f"/api/v3/episodefile?seriesId={s['id']}")


def tokens_for(f):
    cfs = {c["name"] for c in f.get("customFormats", [])}
    mi = f.get("mediaInfo") or {}
    tokens = []
    if not cfs & AUDIO_CF and AUDIO_TOKEN.get(mi.get("audioCodec") or ""):
        tokens.append(AUDIO_TOKEN[mi["audioCodec"]])
    if not cfs & HDR_CF and HDR_TOKEN.get(mi.get("videoDynamicRangeType") or ""):
        tokens.append(HDR_TOKEN[mi["videoDynamicRangeType"]])
    return tokens


def insert(name, tokens):
    m = re.search(r"-[A-Za-z0-9]+$", name)  # keep the -GROUP suffix last
    if m:
        return name[:m.start()] + "." + ".".join(tokens) + name[m.start():]
    return name + "." + ".".join(tokens)


def run(arr):
    changed = reverted = manual = 0
    for f in arr.files():
        scene = f.get("sceneName")
        if not scene:
            continue
        tokens = tokens_for(f)
        if not tokens:
            continue
        first = ((f.get("mediaInfo") or {}).get("audioLanguages") or "").split("/")[0]
        if first not in ("ger", "deu", "", "und"):
            print(f"{arr.name} {f['id']}: first audio track is '{first}', check by hand: {scene}")
            manual += 1
            continue
        f2 = dict(f, sceneName=insert(scene, tokens))
        arr.call("PUT", f"/api/v3/{arr.ep}/{f['id']}", f2)
        after = arr.call("GET", f"/api/v3/{arr.ep}/{f['id']}")
        if after.get("customFormatScore", 0) < f.get("customFormatScore", 0):
            arr.call("PUT", f"/api/v3/{arr.ep}/{f['id']}", f)
            print(f"{arr.name} {f['id']}: reverted, score would drop: {scene}")
            reverted += 1
            continue
        print(f"{arr.name} {f['id']}: {scene} +{'.'.join(tokens)} "
              f"(score {f.get('customFormatScore')} -> {after.get('customFormatScore')})")
        changed += 1
    print(f"{arr.name}: tagged {changed}, reverted {reverted}, to check by hand {manual}")


def main():
    ok = True
    for name in ("radarr", "sonarr"):
        url, key = os.environ.get(f"{name.upper()}_URL"), os.environ.get(f"{name.upper()}_API_KEY")
        if not (url and key):
            continue
        try:
            run(Arr(name, url, key))
        except Exception as e:  # one app down must not hide the other
            print(f"{name}: failed: {e}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
