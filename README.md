Silence Cutter → Final Cut Pro (FCPXML)

What it does
- Takes a video (e.g., .mp4) with a-roll dialogue
- Detects silences using ffmpeg’s silencedetect
- Keeps speech segments with configurable padding/merge rules
- Outputs an FCPXML project you can import into Final Cut Pro with all cuts visible and editable

Quick start (CLI)
1) Install ffmpeg (if not already)
   - brew install ffmpeg
2) Run the script:
   - python3 silence_cutter.py "/path/to/your_clip.mp4" --json
3) Import the generated file (same folder as input):
   - your_clip_silence_cuts.fcpxml
   - In FCP: File → Import → XML… → select the .fcpxml
   - You’ll get an Event named “Silence Cutter” with a Project “your_clip (Silence Cut)”

Quick start (GUI)
1) Launch the UI:
   - python3 gui_tk.py
2) Click “Select Video…” to choose your clip, adjust settings, optionally click “Select Output Folder…”
3) Click “Generate FCPXML” → then “Reveal Output in Finder” and import the XML into FCP (File → Import → XML…)

Tunable parameters
- --threshold: Silence threshold in dB (default: -35). Raise (e.g., -30) if room tone is getting cut; lower (e.g., -40) if silences aren’t found.
- --min-silence: Minimum length (seconds) to consider silence (default: 0.50)
- --pad: Padding added around each speech segment (default: 0.10s per side)
- --merge-gap: Merge speech segments closer than this (seconds; default: 0.30)
- --min-keep: Drop kept segments shorter than this (seconds; default: 0.25)
- --crossfade-frames: Placeholder for adding short crossfades (default: 0)
- --json: Also write a JSON with the final kept segments (for QA)

Notes
- The XML references your original file on disk via a file:// URL. Don’t move/rename it before import.
- Cuts are snapped to the nearest frame for your clip’s FPS so there are no sub-frame offsets.
- If you see off-by-one-frame nicks, tweak --pad (e.g., 0.12–0.16s) or --merge-gap (e.g., 0.40s).
- For very quiet rooms, you may want --threshold -30 and --min-silence 0.35.

Known limitations / roadmap
- Crossfades are not authored yet (placeholder). I can wire in automatic 2–3 frame dissolves between segments if you want.
- If your footage is VFR, we conform to the reported average FPS; if FCP behaves oddly, we can expose a --force-fps option.
- If you want a hard “keep all silences shorter than N seconds,” we can add --max-cut.

Examples
- python3 silence_cutter.py clip.mp4 --threshold -33 --min-silence 0.45 --pad 0.12 --merge-gap 0.35 --json

Troubleshooting
- “ffprobe/ffmpeg not found”: Install via Homebrew → brew install ffmpeg
- FCP won’t import XML: Make sure you choose File → Import → XML…, not media import. If it still fails, send me the .fcpxml.
- Cuts feel too aggressive: Increase --pad to 0.15–0.20s, raise --threshold to -30, and increase --merge-gap to 0.45s.
