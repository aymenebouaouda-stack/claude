"""Lit les sous-titres INCRUSTÉS d'une vidéo : découpe la bande de texte à intervalle régulier
et l'empile en une image horodatée, que Claude lit ensuite (outil Read).

Utile quand la transcription audio est impossible (pas de modèle, réseau bloqué) ou pour
analyser le style de sous-titres d'une vidéo de référence (TikTok, Reels…).

Usage:
    python3 caption_strip.py <video> --top 0.735 --height 0.11 [--fps 2.5] [--chunk 20] -o <dossier>
    → <dossier>/captions_000.png, captions_001.png… (une image par tranche de --chunk secondes)

Repérer --top/--height (fractions de la hauteur) sur une planche contact au préalable.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--top", type=float, required=True, help="Haut de la bande de texte (fraction)")
    ap.add_argument("--height", type=float, default=0.11, help="Hauteur de la bande (fraction)")
    ap.add_argument("--fps", type=float, default=2.5, help="Échantillons par seconde")
    ap.add_argument("--chunk", type=float, default=20.0, help="Secondes par image de sortie")
    ap.add_argument("--width", type=int, default=384)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    args = ap.parse_args()

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "csv=p=0", str(args.video)], capture_output=True, text=True).stdout)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    per = int(args.fps * args.chunk)
    rows = -(-per // 2)
    k, t = 0, 0.0
    while t < dur:
        out = args.out_dir / f"captions_{k:03d}.png"
        # drawtext affiche le temps SOURCE (décalage de la tranche ajouté)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-t", f"{args.chunk:.3f}", "-i", str(args.video),
             "-vf", f"fps={args.fps},crop=iw:ih*{args.height}:0:ih*{args.top},scale={args.width}:-2,"
                    f"drawtext=text='%{{pts\\:hms\\:{t:.3f}}}':x=4:y=4:fontsize=12:fontcolor=yellow:"
                    f"box=1:boxcolor=black,tile=2x{rows}:padding=2",
             "-frames:v", "1", "-y", str(out)], check=True)
        print(f"{out}  ({t:.0f}s → {min(dur, t + args.chunk):.0f}s)")
        k += 1
        t += args.chunk


if __name__ == "__main__":
    main()
