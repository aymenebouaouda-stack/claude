"""Changements de plan (ffmpeg, filtre scene) : liste horodatée + rythme moyen.

Deux usages :
  - analyser le rythme d'une vidéo de référence (combien de coupes, à quelle cadence) ;
  - repérer les plans dans un long rush pour choisir des B-rolls.
Les incrustations légères (titres, sous-titres) ne sont souvent PAS détectées : compléter
avec contact_sheet.py.

Usage:
    python3 scenes.py <video> [--threshold 0.25] [--sheet <out.png>]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--threshold", type=float, default=0.25, help="0.1 = sensible, 0.4 = coupes franches")
    ap.add_argument("--sheet", type=Path, help="Planche des premières images de chaque plan")
    args = ap.parse_args()

    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(args.video), "-an",
         "-vf", f"select='gt(scene,{args.threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", log)]
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(args.video)], capture_output=True, text=True).stdout.strip()
    dur = float(dur)
    starts = [0.0] + cuts
    lens = [b - a for a, b in zip(starts, cuts + [dur])]
    print(f"durée {dur:.1f}s | {len(cuts)} coupes (seuil {args.threshold}) | "
          f"plan moyen {dur / len(starts):.2f}s | plus court {min(lens):.2f}s | plus long {max(lens):.2f}s")
    for a, l in zip(starts, lens):
        print(f"  {int(a // 60):02d}:{a % 60:05.2f}  ({l:.2f}s)")

    if args.sheet:
        args.sheet.parent.mkdir(parents=True, exist_ok=True)
        expr = f"eq(n\\,0)+gt(scene\\,{args.threshold})"
        cols = 5
        rows = -(-len(starts) // cols)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(args.video), "-vf",
             f"select='{expr}',scale=300:-2,drawtext=text='%{{pts\\:hms}}':x=6:y=6:fontsize=18:"
             f"fontcolor=white:box=1:boxcolor=black@0.6,tile={cols}x{rows}:padding=4",
             "-frames:v", "1", "-fps_mode", "vfr", "-y", str(args.sheet)], check=True)
        print(f"planche des plans → {args.sheet}")


if __name__ == "__main__":
    main()
