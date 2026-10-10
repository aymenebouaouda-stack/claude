"""Mesure la synchronisation son/image d'un rendu sur une mire « flash + bip ».

Mire : écran noir avec un flash blanc et un bip au même instant (ex. chaque seconde). On
repère les débuts de flash (luminance moyenne) et les débuts de bip (sortie de silence), puis
on affiche l'écart bip − flash pour chaque événement (> 0 : le son est EN RETARD sur l'image,
< 0 : le son est EN AVANCE).

Usage:
    python3 sync_check.py <video> [--video-thresh 128] [--noise -30]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def flashes(video: Path, thresh: float) -> list[float]:
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    times, out, prev = [], [], 0.0
    t = None
    for line in log.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"YAVG=([\d.]+)", line)
        if m and t is not None:
            y = float(m.group(1))
            if y >= thresh and prev < thresh:
                out.append(t)
            prev = y
    return out


def beeps(video: Path, noise: float) -> list[float]:
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                          f"silencedetect=noise={noise}dB:d=0.02", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--video-thresh", type=float, default=128)
    ap.add_argument("--noise", type=float, default=-30)
    args = ap.parse_args()
    fl, bp = flashes(args.video, args.video_thresh), beeps(args.video, args.noise)
    diffs = []
    for f in fl:
        near = min(bp, key=lambda b: abs(b - f), default=None)
        if near is not None and abs(near - f) < 0.5:
            diffs.append((f, near - f))
    for f, d in diffs:
        print(f"  flash {f:8.3f}s  écart son−image {d * 1000:+7.1f} ms")
    if diffs:
        ds = [d for _, d in diffs]
        print(f"{len(ds)} événements | moyenne {1000 * sum(ds) / len(ds):+.1f} ms | min {1000 * min(ds):+.1f} | "
              f"max {1000 * max(ds):+.1f} (1 image à 30 i/s = 33 ms)")


if __name__ == "__main__":
    main()
