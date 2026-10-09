"""Aide à redresser : pour chaque instant demandé, montre l'image telle quelle puis tournée de
90°, 180° et 270° (sens horaire), côte à côte. Choisir la colonne où la scène est droite et
reporter la valeur dans l'EDL ("rotate": 90|180|270).

Usage:
    python3 orient_check.py <video> --times 2 10 25 -o orient.png [--size 180]
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--times", type=float, nargs="+", required=True)
    ap.add_argument("--size", type=int, default=180)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    S = args.size
    font = ImageFont.truetype(FONT, max(10, S // 10))
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, t in enumerate(args.times):
            f = Path(tmp) / f"{k}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(args.video), "-frames:v", "1",
                            "-vf", f"scale={S}:{S}:force_original_aspect_ratio=decrease", "-y", str(f)], check=True)
            rows.append((t, Image.open(f).convert("RGB")))
    lab = S // 7
    sheet = Image.new("RGB", (4 * S + 60, len(rows) * (S + lab)), (25, 25, 25))
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(rows):
        y = i * (S + lab)
        d.text((4, y + S // 2), f"{t:g}s", font=font, fill=(255, 230, 0))
        for j, rot in enumerate((0, 90, 180, 270)):
            r = im.rotate(-rot, expand=True)  # PIL tourne dans le sens antihoraire
            x = 60 + j * S + (S - r.width) // 2
            sheet.paste(r, (x, y + lab + (S - r.height) // 2 if r.height < S else y + lab))
            if i == 0:
                d.text((60 + j * S + 4, y), f"rotate {rot}", font=font, fill=(120, 220, 255))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
