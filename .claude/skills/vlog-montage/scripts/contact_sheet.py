"""Planche contact : N vignettes horodatées d'une plage de la vidéo, en une seule image PNG.

C'est ainsi que Claude « regarde » une vidéo : il lit la PNG avec l'outil Read.

Usage:
    python3 contact_sheet.py <video> [--start 0] [--end <durée>] [--n 12] [--cols 4] -o sheet.png
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


def duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=360, help="Largeur d'une vignette")
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    end = args.end if args.end is not None else duration(args.video)
    step = (end - args.start) / args.n
    times = [args.start + step * (i + 0.5) for i in range(args.n)]

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, t in enumerate(times):
            f = Path(tmp) / f"f{i:03d}.png"
            label = f"{int(t // 60):02d}\\:{t % 60:05.2f}"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(args.video), "-frames:v", "1",
                 "-vf", f"scale={args.width}:-2,drawtext=text='{label}':x=8:y=8:fontsize=22:"
                        "fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4",
                 "-y", str(f)], check=True)
            frames.append(f)
        rows = -(-len(frames) // args.cols)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-framerate", "1", "-i", str(Path(tmp) / "f%03d.png"),
             "-vf", f"tile={args.cols}x{rows}:padding=4:color=black", "-frames:v", "1",
             "-y", str(args.output)], check=True)
    print(f"planche → {args.output} ({len(times)} vignettes, {args.start:.1f}s → {end:.1f}s)")


if __name__ == "__main__":
    main()
