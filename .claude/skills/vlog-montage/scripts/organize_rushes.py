"""Range des rushes de téléphone dans l'ordre chronologique de TOURNAGE.

Date utilisée, par ordre de fiabilité :
  1. com.apple.quicktime.creationdate (iPhone, heure locale + fuseau)
  2. creation_time du conteneur (UTC, converti en heure de Paris par défaut)
  3. date de modification du fichier (conservée par `unzip`)  ← signalée « incertaine »
Les ex æquo sont départagés par le nom de fichier (IMG_0012 avant IMG_0013).

Crée <sortie>/ordered/NNN_<date>_<heure>_<nom> (liens symboliques, rushes jamais modifiés)
et <sortie>/rushes.md + rushes.json (tableau : n°, date, durée, résolution, rotation, source de la date).

Usage:
    python3 organize_rushes.py <dossier_rushes> -o <edit_dir> [--tz Europe/Paris]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".mts", ".3gp", ".webm"}


def probe(p: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(p)], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def shot_time(p: Path, info: dict, tz: ZoneInfo) -> tuple[datetime, str]:
    tags = {k.lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
    apple = tags.get("com.apple.quicktime.creationdate")
    if apple:
        try:
            return datetime.fromisoformat(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", apple)), "iphone"
        except ValueError:
            pass
    ct = tags.get("creation_time") or next(
        (s.get("tags", {}).get("creation_time") for s in info.get("streams", []) if s.get("tags", {}).get("creation_time")), None)
    if ct and not ct.startswith("1970") and not ct.startswith("1904"):
        return datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone(tz), "conteneur"
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).astimezone(tz), "fichier (incertain)"


def rotation(info: dict) -> int:
    for s in info.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        for sd in s.get("side_data_list", []):
            if "rotation" in sd:
                return int(sd["rotation"]) % 360
        if "rotate" in s.get("tags", {}):
            return int(s["tags"]["rotate"]) % 360
    return 0


def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("-o", "--edit-dir", type=Path, required=True)
    ap.add_argument("--tz", default="Europe/Paris")
    args = ap.parse_args()
    tz = ZoneInfo(args.tz)

    files = [p for p in args.folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT
             and not p.name.startswith("._")]
    rows = []
    for p in files:
        info = probe(p)
        v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        when, src = shot_time(p, info, tz)
        rot = rotation(info)
        w, h = v.get("width"), v.get("height")
        if rot in (90, 270) and w and h:
            w, h = h, w
        rows.append({"file": str(p.resolve()), "name": p.name, "when": when.isoformat(), "date_source": src,
                     "duration": round(float(info.get("format", {}).get("duration", 0) or 0), 2),
                     "width": w, "height": h, "rotation_meta": rot,
                     "fps": v.get("avg_frame_rate"), "audio": any(s.get("codec_type") == "audio"
                                                                  for s in info.get("streams", []))})
    rows.sort(key=lambda r: (r["when"], natural_key(r["name"])))

    ordered = args.edit_dir / "ordered"
    ordered.mkdir(parents=True, exist_ok=True)
    for old in ordered.iterdir():
        if old.is_symlink():
            old.unlink()
    lines = ["| n° | date | heure | durée | format | source date | fichier |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        dt = datetime.fromisoformat(r["when"])
        r["index"] = i
        r["ordered"] = str(ordered / f"{i:03d}_{dt:%Y-%m-%d_%H%M%S}_{r['name']}")
        Path(r["ordered"]).symlink_to(r["file"])
        m, s = divmod(r["duration"], 60)
        jour = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"][dt.weekday()]
        lines.append(f"| {i:03d} | {jour} {dt:%d/%m} | {dt:%H:%M:%S} | {int(m)}:{s:04.1f} | "
                     f"{r['width']}x{r['height']} | {r['date_source']} | {r['name']} |")
    (args.edit_dir / "rushes.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    (args.edit_dir / "rushes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    total = sum(r["duration"] for r in rows)
    uncertain = sum(r["date_source"].startswith("fichier") for r in rows)
    print("\n".join(lines))
    print(f"\n{len(rows)} vidéos, {total / 60:.1f} min au total, {uncertain} date(s) incertaine(s)")


if __name__ == "__main__":
    main()
