"""Détecte les silences d'un rush (ffmpeg silencedetect) et propose les plages parlées.

Sert à repérer les temps morts sans transcription. Les plages proposées ne sont
que des candidats : vérifier visuellement (contact_sheet.py) avant de couper.

Usage:
    python3 silences.py <video> [--noise -35] [--min 0.5] [--pad 0.12] [-o out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def detect(video: Path, noise_db: float, min_dur: float) -> tuple[list[tuple[float, float]], float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    log = proc.stderr
    dur = 0.0
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", log)
    if m:
        dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    if len(ends) < len(starts):  # silence jusqu'à la fin du fichier
        ends.append(dur)
    return [(max(0.0, s), e) for s, e in zip(starts, ends)], dur


def speech_ranges(silences, dur, pad):
    ranges, cur = [], 0.0
    for s, e in silences:
        if s - cur > 0.05:
            ranges.append((max(0.0, cur - pad), min(dur, s + pad)))
        cur = e
    if dur - cur > 0.05:
        ranges.append((max(0.0, cur - pad), dur))
    return [(round(a, 3), round(b, 3)) for a, b in ranges]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--noise", type=float, default=-35.0, help="Seuil de silence en dB (défaut -35)")
    ap.add_argument("--min", type=float, default=0.5, help="Durée mini d'un silence en s (défaut 0.5)")
    ap.add_argument("--pad", type=float, default=0.12, help="Marge gardée autour de la parole (défaut 0.12)")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    sil, dur = detect(args.video, args.noise, args.min)
    keep = speech_ranges(sil, dur, args.pad)
    kept = sum(b - a for a, b in keep)
    print(f"durée {dur:.1f}s | {len(sil)} silences ≥ {args.min}s | parole gardée {kept:.1f}s ({kept / dur:.0%})"
          if dur else "durée inconnue")
    for a, b in keep:
        print(f"  [{a:07.2f}-{b:07.2f}]")
    if args.output:
        args.output.write_text(json.dumps({"source": str(args.video.resolve()), "duration": dur,
                                           "silences": sil, "speech": keep}, indent=2))


if __name__ == "__main__":
    main()
