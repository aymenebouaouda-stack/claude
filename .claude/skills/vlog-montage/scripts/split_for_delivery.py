"""Découpe un montage en parties sous une taille maximale (ex. 30 Mo par fichier envoyé),
en coupant entre deux plans de l'EDL (jamais au milieu d'une phrase), encodage 2 passes.

Usage:
    python3 split_for_delivery.py <montage.mp4> --edl <edl.json> -o <dossier> [--max-mib 28.5]
           [--target-s 265] [--height 1280] [--name "Mon vlog - aperçu"]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--edl", type=Path, required=True)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--max-mib", type=float, default=28.5, help="Taille visée par partie (marge sous la limite)")
    ap.add_argument("--target-s", type=float, default=265.0, help="Durée visée par partie")
    ap.add_argument("--height", type=int, default=1280, help="Hauteur de sortie (largeur au prorata)")
    ap.add_argument("--audio-k", type=int, default=64)
    ap.add_argument("--name", default="aperçu")
    args = ap.parse_args()

    edl = json.loads(args.edl.read_text())
    starts, t = [], 0.0
    for r in edl["ranges"]:
        starts.append(t)
        t += (r["end"] - r["start"]) / float(r.get("speed", 1.0))
    total, cuts = t, [0.0]
    lo, hi = args.target_s * 0.7, args.target_s * 1.07
    while total - cuts[-1] > hi:
        cand = [s for s in starts if cuts[-1] + lo < s <= cuts[-1] + hi]
        goal = cuts[-1] + args.target_s
        cuts.append(min(cand, key=lambda s: abs(s - goal)) if cand else goal)
    cuts.append(total)
    parts = list(zip(cuts, cuts[1:]))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log = args.out_dir / "split2pass"
    for i, (a, b) in enumerate(parts, 1):
        d = b - a
        vk = int(args.max_mib * 1048576 * 8 / d / 1000) - args.audio_k - 16
        out = args.out_dir / f"{args.name} {i} sur {len(parts)}.mp4"
        common = ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", str(args.video),
                  "-vf", f"scale=-2:{args.height}:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
                  "-b:v", f"{vk}k", "-maxrate", f"{int(vk * 1.5)}k", "-bufsize", f"{vk * 3}k"]
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *common, "-pass", "1",
                        "-passlogfile", str(log), "-an", "-f", "mp4", os.devnull], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *common, "-pass", "2",
                        "-passlogfile", str(log), "-c:a", "aac", "-b:a", f"{args.audio_k}k",
                        "-movflags", "+faststart", str(out)], check=True)
        print(f"{out.name} : {int(a // 60):02d}:{a % 60:04.1f} → {int(b // 60):02d}:{b % 60:04.1f}, "
              f"{out.stat().st_size / 2**20:.1f} Mio", flush=True)


if __name__ == "__main__":
    main()
