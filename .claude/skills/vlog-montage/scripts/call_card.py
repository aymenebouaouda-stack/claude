"""Transition « appel entrant » : fond flouté + icône de téléphone qui vibre + nom, avec sonnerie.

À placer entre « attends, X m'appelle » et la vidéo suivante (la nouvelle annoncée). La sonnerie
est SYNTHÉTISÉE ici (trille électronique à deux tons + vibreur), donc sans droits d'auteur ; on
n'imite volontairement ni l'interface ni la sonnerie d'une marque.

Usage:
    python3 call_card.py -o appel.mp4 --name "Prénom" [--bg video.mp4 --bg-start 105 --bg-rotate 270]
                         [--size 1920x1080] [--dur 2.8] [--title "Appel entrant…"] [--font Anton.ttf]
"""

from __future__ import annotations

import argparse
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ICON_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SR = 48000


def ring(dur: float) -> np.ndarray:
    """Deux salves de trille (1 000 / 1 250 Hz alternés à 25 Hz) + vibreur grave, en stéréo float."""
    t = np.arange(int(dur * SR)) / SR
    alt = (np.floor(t * 25) % 2).astype(bool)
    tone = np.where(alt, np.sin(2 * math.pi * 1000 * t), np.sin(2 * math.pi * 1250 * t))
    env = np.zeros_like(t)
    for a in (0.15, 1.35):
        b = a + 0.85
        m = (t >= a) & (t < b)
        env[m] = np.minimum(1, np.minimum((t[m] - a) / 0.02, (b - t[m]) / 0.05))
    buzz = np.sign(np.sin(2 * math.pi * 165 * t)) * 0.12 * ((t % 0.6) < 0.4) * (t > 0.05)
    x = 0.35 * tone * env + buzz
    fade = np.minimum(1, (dur - t) / 0.25)
    x = (x * fade).astype(np.float32)
    return np.stack([x, x], axis=1)


def background(args, W: int, H: int, tmp: Path) -> Image.Image:
    if not args.bg:
        return Image.new("RGB", (W, H), (18, 18, 22))
    rot = {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}.get(args.bg_rotate % 360, "")
    png = tmp / "bg.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(args.bg_start), "-i", str(args.bg), "-frames:v", "1",
                    "-vf", f"{rot}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                    "-y", str(png)], check=True)
    im = Image.open(png).convert("RGB").filter(ImageFilter.GaussianBlur(28))
    return Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", default="Appel entrant…")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--dur", type=float, default=2.8)
    ap.add_argument("--font", default=None, help="police du nom (ex. Anton)")
    ap.add_argument("--bg", type=Path)
    ap.add_argument("--bg-start", type=float, default=0.0)
    ap.add_argument("--bg-rotate", type=int, default=0)
    args = ap.parse_args()
    W, H = (int(v) for v in args.size.split("x"))
    k = min(W, H) / 1080
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        bg = background(args, W, H, tmp)
        name_f = ImageFont.truetype(args.font or ICON_FONT, int(110 * k))
        title_f = ImageFont.truetype(args.font or ICON_FONT, int(48 * k))
        icon_f = ImageFont.truetype(ICON_FONT, int(120 * k))
        n = int(round(args.dur * args.fps))
        cx, cy, r0 = W // 2, int(H * 0.40), int(95 * k)
        for i in range(n):
            t = i / args.fps
            im = bg.copy()
            d = ImageDraw.Draw(im)
            ringing = any(a <= t < a + 0.85 for a in (0.15, 1.35))
            # ondes autour de l'icône pendant la sonnerie
            for j in range(3):
                ph = ((t * 1.6) + j / 3) % 1
                rr = int(r0 * (1 + 0.9 * ph))
                a = int(160 * (1 - ph)) if ringing else 0
                if a:
                    ov = Image.new("RGBA", (W, H))
                    ImageDraw.Draw(ov).ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(52, 199, 89, a),
                                                width=max(2, int(5 * k)))
                    im.paste(ov, (0, 0), ov)
            d = ImageDraw.Draw(im)
            d.ellipse([cx - r0, cy - r0, cx + r0, cy + r0], fill=(52, 199, 89))
            # icône qui « vibre » (petite rotation alternée) pendant la sonnerie
            ang = 12 * math.sin(t * 2 * math.pi * 14) if ringing else 0
            ic = Image.new("RGBA", (2 * r0, 2 * r0))
            ImageDraw.Draw(ic).text((r0, r0), "☎", font=icon_f, fill="white", anchor="mm")
            ic = ic.rotate(ang, resample=Image.BICUBIC)
            im.paste(ic, (cx - r0, cy - r0), ic)
            d.text((cx, cy + int(170 * k)), args.name, font=name_f, fill="white", anchor="mm",
                   stroke_width=int(3 * k), stroke_fill="black")
            d.text((cx, cy + int(255 * k)), args.title, font=title_f, fill=(225, 225, 225), anchor="mm",
                   stroke_width=int(2 * k), stroke_fill="black")
            if t < 0.2:  # entrée en fondu depuis le noir
                im = Image.blend(Image.new("RGB", (W, H)), im, t / 0.2)
            im.save(tmp / f"f{i:04d}.png")
        wav = tmp / "ring.wav"
        pcm = (np.clip(ring(n / args.fps), -1, 1) * 32767).astype("<i2")
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-v", "error", "-framerate", str(args.fps), "-i", str(tmp / "f%04d.png"),
                        "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac",
                        "-b:a", "192k", "-shortest", "-movflags", "+faststart", "-y", str(args.output)], check=True)
    print(f"OK → {args.output} ({n / args.fps:.2f}s)")


if __name__ == "__main__":
    main()
