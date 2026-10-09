"""Planche « une vignette par rush » dans l'ordre de edit/rushes.json (produit par organize_rushes.py).

Sert à vérifier l'ordre chronologique à l'image et à repérer les vidéos filmées de travers.
Vignette prise à 40 % de la durée (--at pour changer), légende « n° nom heure ».

Usage:
    python3 thumb_grid.py --edit-dir <edit_dir> [--per-sheet 40] [--cols 8] [--at 0.4]
    → <edit_dir>/verify/grid_1.jpg, grid_2.jpg…
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--per-sheet", type=int, default=40)
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--at", type=float, default=0.4)
    ap.add_argument("--size", type=int, default=200)
    args = ap.parse_args()

    rows = json.loads((args.edit_dir / "rushes.json").read_text())
    tdir = args.edit_dir / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    S = args.size
    font = ImageFont.truetype(FONT, max(10, S // 13))
    items = []
    for r in rows:
        out = tdir / f"{Path(r['name']).stem}_{args.at:.2f}.jpg"
        if not out.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{r['duration'] * args.at:.2f}", "-i", r["file"],
                            "-frames:v", "1", "-vf", f"scale={S}:{S}:force_original_aspect_ratio=decrease,"
                            f"pad={S}:{S}:(ow-iw)/2:(oh-ih)/2", "-y", str(out)], check=True)
        items.append((r, out))
    (args.edit_dir / "verify").mkdir(exist_ok=True)
    for s, start in enumerate(range(0, len(items), args.per_sheet), 1):
        chunk = items[start:start + args.per_sheet]
        nrows = -(-len(chunk) // args.cols)
        lh = S // 6
        im = Image.new("RGB", (args.cols * S, nrows * (S + lh)), (20, 20, 20))
        d = ImageDraw.Draw(im)
        for k, (r, f) in enumerate(chunk):
            x, y = (k % args.cols) * S, (k // args.cols) * (S + lh)
            im.paste(Image.open(f), (x, y))
            name = Path(r["name"]).stem.replace("_singular_display", "")[:12]
            d.text((x + 4, y + S + 2), f"{r['index']:03d} {name}", font=font, fill=(255, 230, 0))
        path = args.edit_dir / "verify" / f"grid_{s}.jpg"
        im.save(path, quality=85)
        print(path)


if __name__ == "__main__":
    main()
