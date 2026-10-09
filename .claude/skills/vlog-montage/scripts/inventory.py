"""Inventaire des rushes : durée, résolution, fps, orientation, piste audio.

Usage:
    python3 inventory.py <dossier_rushes> [-o <edit_dir>/inventory.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".webm", ".3gp"}


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"file": str(path.resolve()), "duration": float(data["format"].get("duration", 0))}
    if v:
        w, h = int(v["width"]), int(v["height"])
        # Les téléphones stockent souvent la rotation en métadonnée.
        rot = 0
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rot = abs(int(sd["rotation"]))
        if rot in (90, 270):
            w, h = h, w
        num, den = v.get("avg_frame_rate", "0/1").split("/")
        info.update({
            "width": w, "height": h,
            "fps": round(float(num) / float(den), 3) if float(den) else None,
            "orientation": "vertical" if h > w else "horizontal" if w > h else "carré",
            "vcodec": v.get("codec_name"),
            "hdr": v.get("color_transfer") in ("smpte2084", "arib-std-b67"),
        })
    info["audio"] = bool(a)
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    files = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in VIDEO_EXT)
    rows = [probe(p) for p in files]
    for r in rows:
        print(f"{Path(r['file']).name:30s} {r['duration']:8.1f}s  {r.get('width')}x{r.get('height')} "
              f"{r.get('fps')}fps {r.get('orientation')} audio={r['audio']} hdr={r.get('hdr')}")
    print(f"TOTAL: {len(rows)} fichiers, {sum(r['duration'] for r in rows) / 60:.1f} min")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
