#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

SILENCE_RE_START = re.compile(r"silence_start: (?P<start>[0-9.]+)")
SILENCE_RE_END = re.compile(r"silence_end: (?P<end>[0-9.]+) \| silence_duration: (?P<dur>[0-9.]+)")

@dataclass
class MediaInfo:
    duration: float
    fps_num: int
    fps_den: int
    fps: float
    sample_rate: int
    channels: int

@dataclass
class Segment:
    start: float
    end: float

    def duration(self) -> float:
        return max(0.0, self.end - self.start)

# ---------------------- ffprobe / ffmpeg helpers ----------------------

def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def probe_media(path: str) -> MediaInfo:
    # Duration
    cmd_dur = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=nokey=1:noprint_wrappers=1",
        path,
    ]
    p = run(cmd_dur)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for duration/fps: {p.stderr}")
    lines = [x.strip() for x in p.stdout.strip().splitlines() if x.strip()]
    # The output order can vary; parse robustly
    duration = None
    fps_num, fps_den = None, None
    for line in lines:
        if "/" in line and all(tok.isdigit() for tok in line.split("/")):
            num, den = line.split("/")
            fps_num, fps_den = int(num), int(den)
        else:
            try:
                duration = float(line)
            except ValueError:
                pass
    if duration is None or fps_num is None or fps_den is None or fps_den == 0:
        # Fallback: try stream=avg_frame_rate
        cmd_alt = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate",
            "-of", "default=nokey=1:noprint_wrappers=1",
            path,
        ]
        p2 = run(cmd_alt)
        if p2.returncode != 0:
            raise RuntimeError(f"ffprobe fallback failed: {p2.stderr}")
        fr = p2.stdout.strip()
        if "/" in fr:
            num, den = fr.split("/")
            fps_num, fps_den = int(num), int(den) if int(den) != 0 else 1
        else:
            # decimal
            f = float(fr)
            # represent as rational with common NTSC bases
            candidates = [(24000,1001),(30000,1001),(60000,1001),(25,1),(24,1),(30,1)]
            best = min(candidates, key=lambda ab: abs((ab[0]/ab[1]) - f))
            fps_num, fps_den = best
    fps = fps_num / fps_den

    # Audio info
    cmd_a = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "default=nokey=1:noprint_wrappers=1",
        path,
    ]
    pa = run(cmd_a)
    if pa.returncode == 0:
        vals = [x.strip() for x in pa.stdout.strip().splitlines() if x.strip()]
        try:
            sample_rate = int(vals[0]) if len(vals) > 0 else 48000
            channels = int(vals[1]) if len(vals) > 1 else 2
        except Exception:
            sample_rate, channels = 48000, 2
    else:
        sample_rate, channels = 48000, 2

    return MediaInfo(duration=duration, fps_num=fps_num, fps_den=fps_den, fps=fps, sample_rate=sample_rate, channels=channels)


def detect_silence(path: str, noise_db: float, min_silence: float) -> List[Tuple[float,float]]:
    # Run ffmpeg silencedetect on the first audio stream
    filt = f"silencedetect=noise={noise_db}dB:d={min_silence}"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", path,
        "-af", filt,
        "-f", "null", "-"
    ]
    p = run(cmd)
    stderr = p.stderr
    # Parse lines for silence_start and silence_end
    silences = []
    cur_start = None
    for line in stderr.splitlines():
        m1 = SILENCE_RE_START.search(line)
        if m1:
            cur_start = float(m1.group("start"))
            continue
        m2 = SILENCE_RE_END.search(line)
        if m2:
            end = float(m2.group("end"))
            if cur_start is None:
                # might happen at file head; treat start as 0
                cur_start = max(0.0, end - float(m2.group("dur")))
            silences.append((cur_start, end))
            cur_start = None
    # If a trailing open silence wasn't closed, ignore it here; end trunc handled later
    return silences

# ---------------------- segment logic ----------------------

def invert_to_speech(silences: List[Tuple[float,float]], duration: float) -> List[Segment]:
    # Merge overlapping silences first
    silences = sorted(silences)
    merged = []
    for s in silences:
        if not merged or s[0] > merged[-1][1]:
            merged.append(list(s))
        else:
            merged[-1][1] = max(merged[-1][1], s[1])
    silences = [(s[0], s[1]) for s in merged]

    speech = []
    cur = 0.0
    for s_start, s_end in silences:
        if s_start > cur:
            speech.append(Segment(cur, s_start))
        cur = max(cur, s_end)
    if cur < duration:
        speech.append(Segment(cur, duration))
    return speech


def apply_rules(segments: List[Segment], *, pad: float, merge_gap: float, min_keep: float, duration: float) -> List[Segment]:
    # Apply padding, clamp to [0, duration]
    padded = []
    for seg in segments:
        start = max(0.0, seg.start - pad)
        end = min(duration, seg.end + pad)
        if end - start > 0:
            padded.append(Segment(start, end))

    # Merge close segments
    merged: List[Segment] = []
    for seg in padded:
        if not merged:
            merged.append(seg)
            continue
        if seg.start - merged[-1].end <= merge_gap:
            merged[-1].end = max(merged[-1].end, seg.end)
        else:
            merged.append(seg)

    # Drop tiny keeps
    keep = [s for s in merged if s.duration() >= min_keep]
    return keep


def snap_to_frames(segments: List[Segment], fps_num: int, fps_den: int) -> List[Segment]:
    # Snap start/end to nearest frame boundary
    frame = fps_den / fps_num  # seconds per tick? Actually frameDuration = fps_den/fps_num seconds
    # But we want frame seconds = 1/fps
    frame_sec = fps_den / fps_num if fps_num != 0 else 1/30
    frame_sec = 1.0 / (fps_num / fps_den)
    out = []
    for s in segments:
        start_frames = round(s.start / frame_sec)
        end_frames = round(s.end / frame_sec)
        if end_frames <= start_frames:
            end_frames = start_frames + 1
        out.append(Segment(start_frames * frame_sec, end_frames * frame_sec))
    return out

# ---------------------- FCPXML ----------------------

def rational_for_fps(num: int, den: int) -> Tuple[int,int]:
    # Try to keep common NTSC framerates in rational form
    candidates = [
        (24000,1001),(30000,1001),(60000,1001),
        (12000,1001),(48000,1001),(23,1),(24,1),(25,1),(30,1),(50,1),(60,1)
    ]
    f = num/den
    best = min(candidates, key=lambda ab: abs((ab[0]/ab[1])-f))
    return best


def to_fcpx_time(seconds: float, timescale: int) -> str:
    # represent as integer ticks over timescale with 's' suffix
    ticks = int(round(seconds * timescale))
    return f"{ticks}/{timescale}s"


def generate_fcpxml(
    input_path: str,
    segments: List[Segment],
    media: MediaInfo,
    project_name: str,
    crossfade_frames: int = 0,
) -> str:
    # Choose a timescale for timeline math
    # Use 24000 for sub-frame precision, or fps_num if you prefer frame-accurate only.
    # We'll use 24000 and still snap to frames above.
    timescale = 24000

    fps_num, fps_den = rational_for_fps(media.fps_num, media.fps_den)
    frame_duration = f"{fps_den}/{fps_num}s"  # e.g., 1001/24000s for 23.976

    # Asset URL must be file:// absolute path with URL encoding for spaces
    abs_path = str(Path(input_path).resolve())
    url = "file://" + abs_path.replace(" ", "%20")

    spine_items = []
    offset_ticks = 0
    cf_ticks = crossfade_frames * int(round(timescale * (media.fps_den / media.fps_num)))

    for seg in segments:
        start = seg.start
        dur = seg.end - seg.start
        # Build XML clip item
        start_s = to_fcpx_time(start, timescale)
        dur_s = to_fcpx_time(dur, timescale)
        off_s = to_fcpx_time(offset_ticks / timescale, timescale)
        # In FCPXML, use asset-clip with start/duration; offset accumulates
        spine_items.append(f"""
        <asset-clip name="{shlex.quote(os.path.basename(input_path))}" ref="r2" start="{start_s}" duration="{dur_s}" offset="{off_s}"/>
        """.strip())
        offset_ticks += int(round(dur * timescale))

    spine_xml = "\n".join(spine_items)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="{fps_num/ fps_den:.3f}p" frameDuration="{frame_duration}"/>
    <asset id="r2" src="{url}" start="0s" duration="{to_fcpx_time(media.duration, timescale)}" format="r1"/>
  </resources>
  <library>
    <event name="Silence Cutter">
      <project name="{project_name}">
        <sequence duration="{to_fcpx_time(sum((s.end-s.start) for s in segments), timescale)}" format="r1">
          <spine>
            {spine_xml}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    return xml

# ---------------------- CLI ----------------------

def main():
    ap = argparse.ArgumentParser(description="Silence Cutter → FCPXML for Final Cut Pro")
    ap.add_argument("input", help="Input video file (.mp4, .mov, etc.)")
    ap.add_argument("--threshold", type=float, default=-35.0, help="Silence threshold in dB (e.g., -35)")
    ap.add_argument("--min-silence", type=float, default=0.50, help="Min silence length in seconds to detect (e.g., 0.50)")
    ap.add_argument("--pad", type=float, default=0.10, help="Padding added to each kept segment (seconds) on both sides")
    ap.add_argument("--merge-gap", type=float, default=0.30, help="Merge gaps smaller than this (seconds)")
    ap.add_argument("--min-keep", type=float, default=0.25, help="Drop kept segments shorter than this (seconds)")
    ap.add_argument("--crossfade-frames", type=int, default=0, help="Optional: add N-frame crossfades (0 = none; note: basic placeholder)")
    ap.add_argument("--json", action="store_true", help="Also write JSON of segments for debugging")
    args = ap.parse_args()

    ipath = args.input
    if not os.path.isfile(ipath):
        print(f"Input not found: {ipath}", file=sys.stderr)
        sys.exit(1)

    try:
        media = probe_media(ipath)
    except Exception as e:
        print(f"Failed to read media info: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        silences = detect_silence(ipath, args.threshold, args.min_silence)
    except Exception as e:
        print(f"Silence detection failed (is ffmpeg installed?): {e}", file=sys.stderr)
        sys.exit(3)

    speech = invert_to_speech(silences, media.duration)
    speech = apply_rules(
        speech,
        pad=args.pad,
        merge_gap=args.merge_gap,
        min_keep=args.min_keep,
        duration=media.duration,
    )
    speech = snap_to_frames(speech, media.fps_num, media.fps_den)

    # Write outputs
    base = os.path.splitext(ipath)[0]
    fcpxml_path = base + "_silence_cuts.fcpxml"
    project_name = os.path.basename(base) + " (Silence Cut)"

    xml = generate_fcpxml(
        ipath,
        speech,
        media,
        project_name,
        crossfade_frames=args.crossfade_frames,
    )
    with open(fcpxml_path, "w", encoding="utf-8") as f:
        f.write(xml)

    if args.json:
        js = [{"start": round(s.start, 6), "end": round(s.end, 6)} for s in speech]
        with open(base + "_speech_segments.json", "w", encoding="utf-8") as jf:
            json.dump(js, jf, indent=2)

    kept = sum(s.end - s.start for s in speech)
    print(f"Done. Wrote: {fcpxml_path}")
    print(f"Segments kept: {len(speech)}; timeline length: {kept:.2f}s (from {media.duration:.2f}s)")

if __name__ == "__main__":
    main()
