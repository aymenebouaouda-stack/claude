"""Exporte une EDL en projet FCPXML 1.9 (importable dans DaVinci Resolve, Final Cut Pro, Premiere
via conversion) + sous-titres SRT, pour que l'utilisateur reprenne le montage dans un vrai logiciel.

Ce qui passe : l'ordre des plans, leurs points d'entrée/sortie, les cartons (fichiers vidéo),
la rotation des rushes filmés de travers (adjust-transform). Ce qui NE passe PAS (à refaire dans
le logiciel, ou à garder en vidéo déjà rendue) : sous-titres stylés (fournis en .srt simple),
textes à l'écran (fournis dans un 2e .srt), flou des côtés, zooms, tremblements, effets sonores,
normalisation du son, ralentis.

Usage:
    python3 export_fcpxml.py edl.json --ass rendu.ass -o dossier_export [--media-root /chemin/rushes]
                             [--name "Mon vlog"]
--media-root : dossier où l'utilisateur a ses rushes ET les cartons (chemins écrits dans le
projet ; Resolve peut de toute façon relier les médias par leur nom de fichier).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", path],
                         capture_output=True, text=True).stdout
    j = json.loads(out or "{}")
    st = (j.get("streams") or [{}])[0]
    return {"w": st.get("width", 1920), "h": st.get("height", 1080),
            "dur": float(j.get("format", {}).get("duration", 0) or 0)}


def rt(seconds: float, fps: int) -> str:
    """Temps rationnel FCPXML aligné sur l'image (n/fps s)."""
    n = round(seconds * fps)
    f = Fraction(n, fps)
    return f"{f.numerator}/{f.denominator}s" if f.denominator != 1 else f"{f.numerator}s"


def ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ass_to_srt(ass: Path, styles: set[str]) -> str:
    """Convertit les répliques ASS des styles donnés en SRT simple (balises retirées, répliques
    identiques consécutives fusionnées — le surlignage mot à mot en produit une par mot)."""
    cues = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if parts[3] not in styles:
            continue

        def sec(x: str) -> float:
            h, m, s = x.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        text = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", "\n").strip()
        a, b = sec(parts[1]), sec(parts[2])
        if cues and cues[-1][2] == text and a - cues[-1][1] < 0.05:
            cues[-1][1] = b
        else:
            cues.append([a, b, text])
    return "\n".join(f"{i}\n{ts_srt(a)} --> {ts_srt(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("--ass", type=Path)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--media-root", default="")
    ap.add_argument("--name", default="Vlog")
    ap.add_argument("--copy-cards", action="store_true", help="copier les cartons dans out-dir/cartons")
    args = ap.parse_args()
    edl = json.loads(args.edl.read_text())
    o = edl["output"]
    W, H, fps = o["width"], o["height"], int(o.get("fps", 30))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    assets, res, spine, t = {}, [], [], 0.0
    res.append(f'<format id="r0" name="FFVideoFormat{H}p{fps}" frameDuration="1/{fps}s" width="{W}" height="{H}"/>')
    for r in edl["ranges"]:
        src = edl["sources"][r["source"]]
        name = Path(src).name
        is_card = "/cards" in src
        if args.copy_cards and is_card:
            (args.out_dir / "cartons").mkdir(exist_ok=True)
            shutil.copy2(src, args.out_dir / "cartons" / name)
        if src not in assets:
            info = probe(src)
            aid, fid = f"a{len(assets) + 1}", f"f{len(assets) + 1}"
            root = args.media_root.rstrip("/")
            path = (f"{root}/{'cartons/' if is_card else ''}{name}" if root else src)
            url = "file://" + quote(path if path.startswith("/") else "/" + path)
            res.append(f'<format id="{fid}" width="{info["w"]}" height="{info["h"]}"/>')
            res.append(f'<asset id="{aid}" name="{escape(Path(name).stem)}" start="0s" duration="{rt(info["dur"], fps)}" '
                       f'hasVideo="1" hasAudio="1" format="{fid}" audioSources="1" audioChannels="2" audioRate="48000">'
                       f'<media-rep kind="original-media" src="{escape(url)}"/></asset>')
            assets[src] = aid
        speed = float(r.get("speed", 1.0))
        d = max(1, round((r["end"] - r["start"]) / speed * fps)) / fps
        inner = ""
        rot = int(r.get("rotate", 0)) % 360
        if rot:  # FCPXML : rotation positive = sens antihoraire
            inner += f'<adjust-transform rotation="{-rot}"/>'
        if r.get("mute"):
            inner += '<adjust-volume amount="-96dB"/>'
        note = escape(str(r.get("beat", ""))[:120])
        spine.append(f'<asset-clip ref="{assets[src]}" name="{escape(Path(name).stem)}" offset="{rt(t, fps)}" '
                     f'start="{rt(r["start"], fps)}" duration="{rt(d, fps)}" tcFormat="NDF">'
                     f'{inner}<note>{note}</note></asset-clip>')
        t += d
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n<resources>\n'
           + "\n".join(res) + '\n</resources>\n<library>\n<event name="' + escape(args.name) + '">\n'
           f'<project name="{escape(args.name)}">\n<sequence format="r0" duration="{rt(t, fps)}" tcStart="0s" '
           'tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n<spine>\n' + "\n".join(spine)
           + '\n</spine>\n</sequence>\n</project>\n</event>\n</library>\n</fcpxml>\n')
    (args.out_dir / "projet.fcpxml").write_text(xml, encoding="utf-8")
    if args.ass and args.ass.exists():
        (args.out_dir / "sous-titres.srt").write_text(ass_to_srt(args.ass, {"Base", "Fort", "Titre"}), encoding="utf-8")
        (args.out_dir / "textes-a-l-ecran.srt").write_text(ass_to_srt(args.ass, {"Contexte", "Label"}), encoding="utf-8")
    print(f"{len(spine)} plans, {t / 60:.1f} min, {len(assets)} médias → {args.out_dir}")


if __name__ == "__main__":
    main()
