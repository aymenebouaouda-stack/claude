"""Mesure le décalage son/image d'un RENDU réel, plan par plan, en le comparant aux rushes.

Pour chaque plan testé (parlé, vitesse 1, assez long) :
  - décalage du SON : corrélation croisée entre le son du rendu (au temps de sortie prévu par
    l'EDL) et le son du rush (au temps source) ;
  - décalage de l'IMAGE : corrélation croisée du « mouvement » (différence moyenne entre images
    successives, insensible au recadrage/flou/zoom) du rendu et du rush.
Écart son − image > 0 : le son arrive APRÈS l'image ; < 0 : le son est EN AVANCE. Un écart
stable sous ~45 ms est imperceptible ; une dérive qui grandit au fil des plans trahit un
problème de concaténation.

Usage:
    python3 av_offset.py edl.json rendu.mp4 [--n 20] [--min-dur 6]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

SR = 8000


def audio(path: str, start: float, dur: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", path,
                          "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def motion(path: str, start: float, dur: float, fps: int, rot: int = 0) -> np.ndarray:
    r = {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}.get(rot % 360, "")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, start):.3f}", "-t", f"{dur:.3f}", "-i", path,
                          "-an", "-vf", f"{r}fps={fps},scale=48:48,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 48 * 48).astype(np.float32)
    return np.abs(np.diff(f, axis=0)).mean(axis=1)


def lag(ref: np.ndarray, sig: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Décalage (en échantillons) de `sig` par rapport à `ref` maximisant la corrélation normalisée."""
    ref = (ref - ref.mean()) / (ref.std() + 1e-9)
    sig = (sig - sig.mean()) / (sig.std() + 1e-9)
    best, best_c = 0, -9.0
    n = len(ref)
    for k in range(-max_lag, max_lag + 1):
        a = sig[max_lag + k: max_lag + k + n]
        if len(a) < n:
            continue
        c = float(np.dot(ref, a) / n)
        if c > best_c:
            best, best_c = k, c
    return best, best_c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("render")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--min-dur", type=float, default=6.0)
    ap.add_argument("--search", type=float, default=0.5, help="décalage maximal cherché (s)")
    ap.add_argument("--from-time", type=float, default=0.0, help="ne tester que les plans après t (s)")
    args = ap.parse_args()
    edl = json.loads(args.edl.read_text())
    fps = int(edl["output"].get("fps", 30))
    t, cands = 0.0, []
    for r in edl["ranges"]:
        d = max(1, round((r["end"] - r["start"]) / float(r.get("speed", 1.0)) * fps)) / fps
        if d >= args.min_dur and float(r.get("speed", 1.0)) == 1.0 and not r.get("mute"):
            if t >= args.from_time:
                cands.append((t, r, d))
        t += d
    pick = [cands[int(i)] for i in np.linspace(0, len(cands) - 1, min(args.n, len(cands)))]
    M = args.search
    rows = []
    for t0, r, d in pick:
        src = edl["sources"][r["source"]]
        L = min(d - 1.0, 8.0)
        a_out = audio(args.render, t0 + 0.5, L)
        a_src = audio(src, r["start"] + 0.5 - M, L + 2 * M)
        la, ca = lag(a_out, a_src, int(M * SR))
        m_out = motion(args.render, t0 + 0.5, L, fps)
        m_src = motion(src, r["start"] + 0.5 - M, L + 2 * M, fps, int(r.get("rotate", 0)))
        lv, cv = lag(m_out, m_src, int(M * fps))
        # la (lv) > 0 : le contenu du rendu correspond à un instant source PLUS TARDIF que prévu
        a_ms, v_ms = 1000 * la / SR, 1000 * lv / fps
        rows.append((t0, r["source"], a_ms, v_ms, ca, cv))
        print(f"{int(t0 // 60):3d}:{t0 % 60:05.2f} {r['source'][:26]:26s} son {a_ms:+7.1f} ms (r={ca:.2f})  "
              f"image {v_ms:+7.1f} ms (r={cv:.2f})  →  son−image {v_ms - a_ms:+7.1f} ms")
    ok = [x for x in rows if x[4] > 0.5 and x[5] > 0.3]
    if ok:
        dd = [x[3] - x[2] for x in ok]
        print(f"\n{len(ok)} plans fiables | son−image : moyenne {np.mean(dd):+.1f} ms, "
              f"min {min(dd):+.1f}, max {max(dd):+.1f} (1 image = {1000 / fps:.0f} ms)")


if __name__ == "__main__":
    main()
