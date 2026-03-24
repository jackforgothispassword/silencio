#!/usr/bin/env python3
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

WORKDIR = Path(__file__).resolve().parent
CUTTER = WORKDIR / "silence_cutter.py"

DEFAULTS = {
    "threshold": "-35",
    "min_silence": "0.50",
    "pad": "0.10",
    "merge_gap": "0.30",
    "min_keep": "0.25",
    "crossfade_frames": "0",
}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Silence Cutter → FCPXML for Final Cut Pro")
        self.geometry("560x320")
        self.resizable(False, False)

        self.file_var = tk.StringVar()
        self.vars = {k: tk.StringVar(value=v) for k, v in DEFAULTS.items()}
        self.output_var = tk.StringVar(value="")

        self._build()

    def _build(self):
        pad = 8

        # File selector (button only, no text field)
        frm_file = tk.Frame(self)
        frm_file.pack(fill="x", padx=pad, pady=(pad, 4))
        tk.Button(frm_file, text="Select Video…", command=self.choose_file, width=18).pack(side="left")
        tk.Label(frm_file, textvariable=self.file_var, anchor="w", fg="#444").pack(side="left", padx=8)

        # Params grid
        grid = tk.Frame(self)
        grid.pack(fill="x", padx=pad, pady=4)

        def row(r, label, key, hint=None):
            tk.Label(grid, text=label, anchor="w", width=24).grid(row=r, column=0, sticky="w", pady=2)
            tk.Entry(grid, textvariable=self.vars[key], width=12).grid(row=r, column=1, sticky="w")
            if hint:
                tk.Label(grid, text=hint, fg="#666").grid(row=r, column=2, sticky="w")

        row(0, "Silence threshold (dB):", "threshold", "e.g. -35; raise to -30 if too aggressive")
        row(1, "Min silence (sec):", "min_silence", "e.g. 0.50")
        row(2, "Padding (sec per side):", "pad", "e.g. 0.10–0.15")
        row(3, "Merge gap (sec):", "merge_gap", "e.g. 0.30–0.45")
        row(4, "Min keep (sec):", "min_keep", "e.g. 0.25")
        row(5, "Crossfade (frames):", "crossfade_frames", "0 for hard cuts")

        # Output folder selector
        outdir = tk.Frame(self)
        outdir.pack(fill="x", padx=pad, pady=(4, 4))
        self.outdir_var = tk.StringVar(value="")
        tk.Button(outdir, text="Select Output Folder…", command=self.choose_output_dir, width=18).pack(side="left")
        tk.Label(outdir, textvariable=self.outdir_var, anchor="w", fg="#444").pack(side="left", padx=8)

        # Actions
        actions = tk.Frame(self)
        actions.pack(fill="x", padx=pad, pady=(8, 4))
        tk.Button(actions, text="Generate FCPXML", command=self.run_cutter, width=18).pack(side="left")
        tk.Button(actions, text="Reveal Output in Finder", command=self.reveal_output).pack(side="left", padx=8)

        # Output label
        out = tk.Frame(self)
        out.pack(fill="x", padx=pad, pady=(4, pad))
        tk.Label(out, text="Output:").pack(anchor="w")
        tk.Entry(out, textvariable=self.output_var, width=80).pack(fill="x")

    def choose_file(self):
        path = filedialog.askopenfilename(title="Choose video",
                                          filetypes=[("Video", ".mp4 .mov .m4v .mxf .avi .mkv"), ("All files", "*.*")])
        if path:
            self.file_var.set(path)

    def choose_output_dir(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.outdir_var.set(path)

    def run_cutter(self):
        ipath = self.file_var.get().strip()
        if not ipath:
            messagebox.showerror("Missing file", "Please select a video file.")
            return
        if not os.path.isfile(ipath):
            messagebox.showerror("Not found", f"File not found:\n{ipath}")
            return
        # Build command
        cmd = [sys.executable, str(CUTTER), ipath,
               "--threshold", self.vars["threshold"].get().strip(),
               "--min-silence", self.vars["min_silence"].get().strip(),
               "--pad", self.vars["pad"].get().strip(),
               "--merge-gap", self.vars["merge_gap"].get().strip(),
               "--min-keep", self.vars["min_keep"].get().strip(),
               "--crossfade-frames", self.vars["crossfade_frames"].get().strip(),
               "--json"]
        outdir = self.outdir_var.get().strip()
        if outdir:
            cmd.extend(["--output-dir", outdir])
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except Exception as e:
            messagebox.showerror("Error running cutter", str(e))
            return

        if p.returncode != 0:
            msg = p.stderr.strip() or p.stdout.strip() or f"Exited with code {p.returncode}"
            messagebox.showerror("Cutter failed", msg)
            return

        # Parse stdout for the output path line
        out_path = None
        for line in p.stdout.splitlines():
            if line.startswith("Done. Wrote:"):
                out_path = line.split(":", 1)[1].strip()
                break
        if out_path and os.path.isfile(out_path):
            self.output_var.set(out_path)
            messagebox.showinfo("Success", f"Generated FCPXML:\n{out_path}\n\nIn Final Cut: File → Import → XML…")
        else:
            messagebox.showinfo("Completed", p.stdout or "Done. Check the output folder.")

    def reveal_output(self):
        out_path = self.output_var.get().strip()
        if not out_path or not os.path.exists(out_path):
            messagebox.showerror("No output", "Run the cutter first, or the output path is missing.")
            return
        subprocess.run(["open", "-R", out_path])

if __name__ == "__main__":
    app = App()
    app.mainloop()
