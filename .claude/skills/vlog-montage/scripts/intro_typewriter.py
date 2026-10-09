"""Carton d'intro « machine à écrire » : le texte s'écrit lettre par lettre avec un bruit de clavier
synthétisé (aucun fichier son externe → pas de droits d'auteur).

Usage:
    python3 intro_typewriter.py -o intro.mp4 --lines "LE MARIAGE DE NASSER" "18 — 26 septembre 2026"
        [--size 1080x1920] [--fps 30] [--cps 14] [--hold 1.5] [--font <ttf>] [--bg <image|video>]

Chaque ligne s'écrit après la précédente ; la 1re en grand, les suivantes plus petites.
--bg : image ou vidéo de fond (assombrie), sinon fond noir.
"""

from __future__ import annotations

import argparse
import random
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
SR = 48000


def keystroke(rng: random.Random, strong: bool = False) -> list[float]:
    """Clic de touche : attaque bruitée très courte + petit « toc » grave, ~40 ms."""
    n = int(SR * 0.045)
    out, lp = [], 0.0
    f = rng.uniform(0.25, 0.45)  # coefficient de filtre passe-bas → timbre variable
    amp = rng.uniform(0.35, 0.55) * (1.4 if strong else 1.0)
    for i in range(n):
        t = i / SR
        env = (1 - i / n) ** 6
        noise = rng.uniform(-1, 1)
        lp += f * (noise - lp)
        thump = 0.5 * (2.718 ** (-t * 90)) * __import__("math").sin(2 * 3.14159 * 140 * t)
        out.append(amp * (env * (noise - lp) * 0.9 + thump))
    return out


def write_wav(path: Path, samples: list[float]) -> None:
    peak = max(1e-9, max(abs(s) for s in samples))
    k = 0.7 / peak if peak > 0.7 else 1.0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * k)) * 32767)) for s in samples))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--lines", nargs="+", required=True)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--cps", type=float, default=14.0, help="caractères par seconde")
    ap.add_argument("--hold", type=float, default=1.5, help="pause finale (s)")
    ap.add_argument("--font", default=FONT)
    ap.add_argument("--bg", type=Path, help="Image ou vidéo de fond (assombrie)")
    ap.add_argument("--bg-start", type=float, default=0.0, help="Début dans la vidéo de fond (s)")
    ap.add_argument("--bg-rotate", type=int, default=0, help="Rotation du fond : 90|180|270")
    ap.add_argument("--bg-sound", type=float, default=0.0,
                    help="Garder le son du fond à ce volume (0 = muet, 0.3 = ambiance discrète)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    W, H = (int(x) for x in args.size.split("x"))
    rng = random.Random(args.seed)
    fonts = []
    for i, line in enumerate(args.lines):  # taille visée, réduite si la ligne dépasse 88 % de la largeur
        size = int(W * (0.075 if i == 0 else 0.052))
        f = ImageFont.truetype(args.font, size)
        while f.getlength(line) > W * 0.88 and size > 10:
            size -= 2
            f = ImageFont.truetype(args.font, size)
        fonts.append(f)

    # Planning des frappes : (temps, ligne, nb_caractères visibles)
    events, t = [], 0.6
    for li, line in enumerate(args.lines):
        for ci in range(1, len(line) + 1):
            ch = line[ci - 1]
            events.append((t, li, ci, ch))
            t += (1 / args.cps) * rng.uniform(0.6, 1.5) * (2.2 if ch in " ,—-" else 1.0)
        t += 0.45  # « retour chariot »
    total = t + args.hold

    # Audio
    samples = [0.0] * int(SR * (total + 0.2))
    for (et, li, ci, ch) in events:
        if ch == " ":
            continue
        k = keystroke(rng, strong=ch.isupper())
        s0 = int(et * SR)
        for i, v in enumerate(k):
            if s0 + i < len(samples):
                samples[s0 + i] += v

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        wav = tmp / "keys.wav"
        write_wav(wav, samples)
        nframes = int(total * args.fps)
        heights = [f.getbbox("Ag")[3] for f in fonts]
        block = sum(int(h * 1.5) for h in heights)
        y0 = (H - block) // 2
        for fi in range(nframes):
            ft = fi / args.fps
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0 if args.bg else 255))
            d = ImageDraw.Draw(img)
            shown = {}
            for (et, li, ci, ch) in events:
                if et <= ft:
                    shown[li] = ci
            y = y0
            cur_line = max(shown) if shown else 0
            for li, line in enumerate(args.lines):
                txt = line[:shown.get(li, 0)]
                full_w = fonts[li].getlength(line)
                x = (W - full_w) / 2  # ancré sur la ligne COMPLÈTE : le texte ne glisse pas
                d.text((x, y), txt, font=fonts[li], fill=(255, 255, 255, 255))
                if li == cur_line and int(ft * 2) % 2 == 0:  # curseur clignotant
                    cx = x + fonts[li].getlength(txt) + 4
                    d.rectangle([cx, y + heights[li] * 0.1, cx + max(3, W // 200), y + heights[li]],
                                fill=(255, 255, 255, 255))
                y += int(heights[li] * 1.5)
            img.save(tmp / f"f{fi:05d}.png")

        cmd = ["ffmpeg", "-v", "error"]
        if args.bg:
            loop = ["-loop", "1"] if args.bg.suffix.lower() in (".png", ".jpg", ".jpeg") else ["-stream_loop", "-1"]
            cmd += [*loop, "-ss", f"{args.bg_start:.3f}", "-t", f"{total:.3f}", "-i", str(args.bg)]
        cmd += ["-framerate", str(args.fps), "-i", str(tmp / "f%05d.png"), "-i", str(wav)]
        if args.bg:
            rot = {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}.get(args.bg_rotate % 360, "")
            fc = (f"[0:v]{rot}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"eq=brightness=-0.10:saturation=0.65,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.5:t=fill,"
                  f"boxblur=6:1,fps={args.fps}[bg];[bg][1:v]overlay=0:0,"
                  f"format=yuv420p[v]")
            amap = "2:a"
            if args.bg_sound > 0:
                fc += (f";[0:a]volume={args.bg_sound},aresample=48000[bga];[2:a]aresample=48000[ka];"
                       f"[bga][ka]amix=inputs=2:duration=shortest:normalize=0[am]")
                amap = "[am]"
        else:
            fc = "[0:v]format=yuv420p[v]"
            amap = "1:a"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", amap, "-t", f"{total:.3f}",
                "-c:v", "libx264", "-crf", "18", "-r", str(args.fps), "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2", "-video_track_timescale", str(args.fps * 1000),
                "-y", str(args.output)]
        subprocess.run(cmd, check=True)
    print(f"intro → {args.output} ({total:.1f}s, {len(events)} frappes)")


if __name__ == "__main__":
    main()
