"""Cartons « vlog » courts, avec son synthétisé (donc sans droits) :

  transition : page de transition entre deux scènes (« Direction l'épicerie », « Retour à la
               maison »…) : fond uni en dégradé, texte qui glisse, petit jingle + « whoosh ».
  timecard   : carton façon dessin animé (« 2 HOURS LATER… ») : fond turquoise, bulles qui
               montent, texte jaune contouré qui ondule, petit jingle « pizzicato ».
  sfx        : écrit seulement un effet sonore WAV (--name boom : impact grave pour un moment fort).
  pause      : image figée d'un rush (assombrie, icône ⏸ « PAUSE », clic + scratch) puis une
               image plein écran (ex. un dessin humoristique) avec un « ba-dum-tss ».

Usage:
    python3 fun_cards.py transition -o t.mp4 --text "Direction l'épicerie" [--sub "à vélo…"] [--dur 3]
    python3 fun_cards.py pause -o p.mp4 --video rush.mp4 --at 122.6 [--rotate 270] --image dessin.png
                         [--pause-dur 1.2] [--image-dur 5]
Options communes : --size 1920x1080 --fps 30 --font Anton.ttf --font2 Montserrat.ttf --accent #FFD400
"""

from __future__ import annotations

import argparse
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

SR = 48000


def env(t: np.ndarray, a: float, d: float) -> np.ndarray:
    return np.where(t < a, t / max(a, 1e-6), np.exp(-(t - a) / d))


def note(f: float, dur: float, vol: float = 0.3) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    x = sum(np.sin(2 * math.pi * f * h * t) / h ** 1.5 for h in (1, 2, 3, 4))
    return (vol * x * env(t, 0.01, dur / 3)).astype(np.float32)


def noise_sweep(dur: float, vol: float = 0.25, up: bool = True) -> np.ndarray:
    n = int(dur * SR)
    x = np.random.default_rng(3).standard_normal(n).astype(np.float32)
    # filtre passe-bande glissant approximé par une moyenne mobile de largeur variable
    out = np.zeros(n, np.float32)
    w = np.linspace(40, 4, n) if up else np.linspace(4, 40, n)
    c = np.cumsum(np.concatenate([[0], x]))
    for i in range(0, n, 64):
        k = int(w[i])
        j = np.arange(i, min(n, i + 64))
        lo = np.maximum(0, j - k)
        out[j] = (c[j + 1] - c[lo]) / (j + 1 - lo)
    t = np.arange(n) / n
    return vol * out / (np.abs(out).max() + 1e-9) * np.sin(math.pi * t) ** 2


def mix(total: float, parts: list[tuple[float, np.ndarray]]) -> np.ndarray:
    y = np.zeros(int(total * SR) + 1, np.float32)
    for t0, x in parts:
        i = int(t0 * SR)
        x = x[: max(0, len(y) - i)]
        y[i:i + len(x)] += x
    return np.clip(y, -1, 1)


def jingle(total: float) -> np.ndarray:
    """Whoosh + arpège majeur joyeux (do-mi-sol-do) + accord final."""
    parts = [(0.0, noise_sweep(0.6, 0.35))]
    for k, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        parts.append((0.35 + 0.14 * k, note(f, 0.5, 0.22)))
    for f in (523.25, 659.25, 783.99):
        parts.append((0.95, note(f, 1.6, 0.13)))
    return mix(total, parts)


def rimshot(total: float, t0: float) -> np.ndarray:
    def tom(f: float) -> np.ndarray:
        t = np.arange(int(0.25 * SR)) / SR
        return (0.5 * np.sin(2 * math.pi * f * t * (1 - 0.3 * t)) * np.exp(-t * 18)).astype(np.float32)
    n = int(0.9 * SR)
    cym = (np.random.default_rng(5).standard_normal(n) * np.exp(-np.arange(n) / SR * 4.5) * 0.18).astype(np.float32)
    cym = np.diff(np.concatenate([[0], cym])).astype(np.float32)  # plus brillant
    return mix(total, [(t0, tom(190)), (t0 + 0.16, tom(140)), (t0 + 0.34, cym)])


def click_scratch(total: float) -> np.ndarray:
    t = np.arange(int(0.03 * SR)) / SR
    click = (0.6 * np.sin(2 * math.pi * 2200 * t) * np.exp(-t * 200)).astype(np.float32)
    tt = np.arange(int(0.35 * SR)) / SR
    scratch = (0.3 * np.sin(2 * math.pi * (900 - 2000 * tt) * tt) * np.sin(math.pi * tt / 0.35)).astype(np.float32)
    return mix(total, [(0.0, scratch), (0.05, click)])


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.stack([x, x], 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def encode(frames_dir: Path, wav: Path, fps: int, out: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"), "-i", str(wav),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", "-y", str(out)], check=True)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def transition(args, W: int, H: int, tmp: Path) -> float:
    k = min(W, H) / 1080
    acc = hex_rgb(args.accent)
    big = ImageFont.truetype(args.font, int(118 * k))
    while big.getlength(args.text.upper()) > W * 0.86:
        big = ImageFont.truetype(args.font, big.size - 4)
    small = ImageFont.truetype(args.font2 or args.font, int(50 * k))
    # fond : dégradé diagonal bleu nuit → violet
    y, x = np.mgrid[0:H, 0:W]
    g = (x / W * 0.6 + y / H * 0.4)[..., None]
    base = (np.array([14, 18, 42]) * (1 - g) + np.array([58, 22, 78]) * g).astype(np.uint8)
    bg = Image.fromarray(base)
    n = int(round(args.dur * args.fps))
    for i in range(n):
        t = i / args.fps
        im = bg.copy()
        d = ImageDraw.Draw(im)
        p = min(1.0, max(0.0, (t - 0.15) / 0.45))
        ease = 1 - (1 - p) ** 3
        bar_w = int(W * 0.5 * ease)
        d.rectangle([W // 2 - bar_w, H // 2 + int(80 * k), W // 2 + bar_w, H // 2 + int(92 * k)], fill=acc)
        dx = int((1 - ease) * -W * 0.35)
        d.text((W // 2 + dx, H // 2 - int(10 * k)), args.text.upper(), font=big, fill=(255, 255, 255), anchor="ms")
        if args.sub:
            p2 = min(1.0, max(0.0, (t - 0.55) / 0.4))
            col = tuple(int(c * p2) for c in acc)
            d.text((W // 2, H // 2 + int(160 * k)), args.sub, font=small, fill=col, anchor="ms")
        fade = min(1.0, t / 0.15, (args.dur - t) / 0.25)
        if fade < 1:
            im = ImageEnhance.Brightness(im).enhance(max(0.0, fade))
        im.save(tmp / f"f{i:04d}.png")
    write_wav(tmp / "a.wav", jingle(n / args.fps))
    return n / args.fps


def boom(total: float = 1.6) -> np.ndarray:
    t = np.arange(int(total * SR)) / SR
    f = 55 + 70 * np.exp(-t * 9)
    low = 0.8 * np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
    hit = np.random.default_rng(9).standard_normal(len(t)) * np.exp(-t * 25) * 0.35
    return np.clip((low + hit).astype(np.float32), -1, 1)


def timecard(args, W: int, H: int, tmp: Path) -> float:
    k = min(W, H) / 1080
    rng = np.random.default_rng(11)
    y, x = np.mgrid[0:H, 0:W]
    r = np.sqrt(((x - W / 2) / W) ** 2 + ((y - H / 2) / H) ** 2)
    base = (np.array([40, 200, 205]) * (1 - r)[..., None] + np.array([10, 90, 120]) * r[..., None]).clip(0, 255)
    bg = Image.fromarray(base.astype(np.uint8))
    font = ImageFont.truetype(args.font, int(150 * k))
    text = args.text or "2 HOURS LATER…"
    while font.getlength(text) > W * 0.85:
        font = ImageFont.truetype(args.font, font.size - 4)
    bubbles = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(10, 45) * k, rng.uniform(60, 160) * k) for _ in range(28)]
    n = int(round(args.dur * args.fps))
    for i in range(n):
        t = i / args.fps
        im = bg.copy()
        ov = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(ov)
        for bx, by, br, sp in bubbles:
            yy = (by - sp * t) % (H + 100) - 50
            xx = bx + 12 * k * math.sin(t * 2 + by)
            d.ellipse([xx - br, yy - br, xx + br, yy + br], outline=(255, 255, 255, 150), width=max(2, int(4 * k)))
        im.paste(ov, (0, 0), ov)
        # texte lettre par lettre, chaque lettre ondule
        tx = (W - font.getlength(text)) / 2
        lay = Image.new("RGBA", (W, H))
        dl = ImageDraw.Draw(lay)
        for j, ch in enumerate(text):
            dy = 14 * k * math.sin(t * 6 + j * 0.6)
            dl.text((tx, H / 2 + dy), ch, font=font, fill=(255, 221, 0), anchor="ls",
                    stroke_width=int(9 * k), stroke_fill=(120, 60, 0))
            tx += font.getlength(ch)
        pop = min(1.0, t / 0.25)
        if pop < 1:
            sc = 0.6 + 0.4 * pop
            lay = lay.resize((int(W * sc), int(H * sc)))
            full = Image.new("RGBA", (W, H))
            full.paste(lay, ((W - lay.width) // 2, (H - lay.height) // 2))
            lay = full
        im.paste(lay, (0, 0), lay)
        fade = min(1.0, (args.dur - t) / 0.25)
        if fade < 1:
            im = ImageEnhance.Brightness(im).enhance(max(0.0, fade))
        im.save(tmp / f"f{i:04d}.png")
    parts = []
    for j, f in enumerate((392.0, 523.25, 659.25, 783.99, 659.25, 783.99, 1046.5)):
        parts.append((0.1 + 0.18 * j, note(f, 0.35, 0.2)))
    write_wav(tmp / "a.wav", mix(n / args.fps, parts))
    return n / args.fps


def pause(args, W: int, H: int, tmp: Path) -> float:
    k = min(W, H) / 1080
    rot = {90: "transpose=1,", 180: "hflip,vflip,", 270: "transpose=2,"}.get(args.rotate % 360, "")
    png = tmp / "still.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(args.at), "-i", str(args.video), "-frames:v", "1", "-vf",
                    f"{rot}scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
                    "-y", str(png)], check=True)
    still = Image.open(png).convert("RGB")
    draw_im = Image.open(args.image).convert("RGB").resize((W, H))
    font = ImageFont.truetype(args.font, int(110 * k))
    n1, n2 = int(round(args.pause_dur * args.fps)), int(round(args.image_dur * args.fps))
    for i in range(n1 + n2):
        t = i / args.fps
        if i < n1:
            im = ImageEnhance.Brightness(ImageEnhance.Color(still).enhance(0.3)).enhance(0.55)
            d = ImageDraw.Draw(im)
            bw, bh, gap = int(34 * k), int(130 * k), int(34 * k)
            cx, cy = W // 2, H // 2 - int(40 * k)
            d.rectangle([cx - gap // 2 - bw, cy - bh // 2, cx - gap // 2, cy + bh // 2], fill="white")
            d.rectangle([cx + gap // 2, cy - bh // 2, cx + gap // 2 + bw, cy + bh // 2], fill="white")
            d.text((cx, cy + bh // 2 + int(120 * k)), "PAUSE", font=font, fill="white", anchor="ms")
        else:
            t2 = t - args.pause_dur
            z = 1.0 + 0.04 * min(1.0, t2 / args.image_dur)  # léger zoom lent
            cw, ch = int(W / z), int(H / z)
            im = draw_im.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H))
            if t2 < 0.12:  # flash blanc d'entrée
                im = Image.blend(Image.new("RGB", (W, H), "white"), im, t2 / 0.12)
            if args.image_dur - t2 < 0.25:
                im = ImageEnhance.Brightness(im).enhance(max(0.0, (args.image_dur - t2) / 0.25))
        im.save(tmp / f"f{i:04d}.png")
    total = (n1 + n2) / args.fps
    write_wav(tmp / "a.wav", np.clip(click_scratch(total) + rimshot(total, args.pause_dur + 0.25), -1, 1))
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["transition", "pause", "timecard", "sfx"])
    ap.add_argument("--name", default="boom")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--font")
    ap.add_argument("--font2")
    ap.add_argument("--accent", default="#FFD400")
    ap.add_argument("--text", default="")
    ap.add_argument("--sub", default="")
    ap.add_argument("--dur", type=float, default=3.0)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--at", type=float, default=0.0)
    ap.add_argument("--rotate", type=int, default=0)
    ap.add_argument("--image", type=Path)
    ap.add_argument("--pause-dur", type=float, default=1.2)
    ap.add_argument("--image-dur", type=float, default=5.0)
    args = ap.parse_args()
    W, H = (int(v) for v in args.size.split("x"))
    if args.kind == "sfx":
        write_wav(args.output, {"boom": boom}[args.name]())
        print(f"OK → {args.output}")
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        total = {"transition": transition, "pause": pause, "timecard": timecard}[args.kind](args, W, H, tmp)
        encode(tmp, tmp / "a.wav", args.fps, args.output)
    print(f"OK → {args.output} ({total:.2f}s)")


if __name__ == "__main__":
    main()
