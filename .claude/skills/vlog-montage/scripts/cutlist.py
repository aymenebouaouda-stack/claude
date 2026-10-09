"""Construit une EDL à partir d'une liste de coupes exprimées en TEXTE, calées au mot près.

Chaque entrée de la liste de coupes :
  {"clip": "IMG_7987", "from": "là ce qu'on vient", "to": "pas de mariage", ...options de plan}
  - "from"/"to" : début/fin d'une phrase prononcée (recherche approximative, insensible à la
    casse et à la ponctuation, après la coupe précédente du même rush) OU un nombre de secondes.
    "from" absent = début du rush ; "to" absent = fin du rush.
  - toute autre clé (rotate, fit, filter, subs, mute, speed, beat, note, gain_db) est recopiée
    dans le plan de l'EDL.
  - {"card": "chemin.mp4"} insère un carton/intro déjà rendu (plan entier).
Les bords sont calés sur les mots (marge 0,12 s avant, 0,20 s après) sans mordre le mot voisin.

Usage:
    python3 cutlist.py cuts.json --edit-dir <edit> --base base.json -o edl.json
    (base.json = clés communes de l'EDL : output, subtitles, normalize_audio, jobs…)
Affiche chaque plan retenu avec sa durée et la durée totale.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from pathlib import Path

PAD_BEFORE, PAD_AFTER, GUARD = 0.12, 0.20, 0.04


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def duration(p: str) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                capture_output=True, text=True).stdout.strip())


def find(words: list[dict], phrase: str, after: float, last: bool) -> tuple[int, int] | None:
    """Indices (i, j) des mots couvrant la phrase, première occurrence commençant après `after`."""
    target = norm(phrase)
    toks = [norm(w["text"]) for w in words]
    for i, w in enumerate(words):
        if w["start"] < after - 0.01 or not toks[i]:
            continue
        acc = ""
        for j in range(i, len(words)):
            acc += toks[j]
            if acc == target or (acc.startswith(target) and len(target) >= 6):
                return i, j
            if not target.startswith(acc):
                break
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cuts", type=Path)
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    rushes = {Path(r["name"]).stem: r for r in json.loads((args.edit_dir / "rushes.json").read_text())}
    cuts = json.loads(args.cuts.read_text())
    edl = json.loads(args.base.read_text())
    edl["sources"], edl["ranges"] = {}, []
    last_end: dict[str, float] = {}
    total, errors = 0.0, []
    for c in cuts:
        if "card" in c:
            name = Path(c["card"]).stem
            edl["sources"][name] = str(Path(c["card"]).resolve())
            d = duration(c["card"])
            rng = {"source": name, "start": 0.0, "end": round(d, 3), "subs": False, "beat": c.get("beat", "CARTON"),
                   **{k: v for k, v in c.items() if k not in ("card", "beat")}}
            rng.setdefault("fit", "fill")
            edl["ranges"].append(rng)
            total += d
            print(f"  [carton] {name:28s} {d:6.1f}s")
            continue
        clip = c["clip"]
        r = rushes[clip]
        edl["sources"][clip] = r["file"]
        tf = args.edit_dir / "transcripts" / f"{clip}.json"
        words = json.loads(tf.read_text())["words"] if tf.exists() else []
        after = last_end.get(clip, 0.0)

        def edge(key: str, is_start: bool) -> float:
            v = c.get(key)
            if v is None:
                return after if is_start and after else (0.0 if is_start else r["duration"])
            if isinstance(v, (int, float)):
                return float(v)
            hit = find(words, v, after if is_start else start_hint, not is_start)
            if not hit and is_start and after:  # passage déjà utilisé plus tôt (ex. accroche) : chercher depuis le début
                hit = find(words, v, 0.0, False)
            if not hit:
                errors.append(f"{clip} : « {v} » introuvable")
                return after if is_start else r["duration"]
            i, j = hit
            if is_start:
                prev_end = words[i - 1]["end"] if i > 0 else 0.0
                return max(prev_end + GUARD if i > 0 else 0.0, words[i]["start"] - PAD_BEFORE, 0.0)
            nxt = words[j + 1]["start"] if j + 1 < len(words) else r["duration"]
            return min(words[j]["end"] + PAD_AFTER, nxt - GUARD if j + 1 < len(words) else r["duration"])

        start_hint = after
        s = edge("from", True)
        start_hint = s
        e = edge("to", False)
        if e <= s + 0.2:
            errors.append(f"{clip} : plage vide ({s:.2f} → {e:.2f})")
            continue
        last_end[clip] = e
        rng = {"source": clip, "start": round(s, 3), "end": round(e, 3),
               **{k: v for k, v in c.items() if k not in ("clip", "from", "to")}}
        if "fit" not in rng and r.get("width") and r.get("height"):
            w_, h_ = r["width"], r["height"]
            if int(c.get("rotate", 0)) % 180 == 90:
                w_, h_ = h_, w_
            if h_ > w_:  # rush vertical (après redressement) : plein cadre en sortie verticale
                rng["fit"] = "fill"
        edl["ranges"].append(rng)
        d = (e - s) / float(c.get("speed", 1.0))
        total += d
        print(f"  {clip:30s} {s:7.2f} → {e:7.2f}  ({d:5.1f}s) {c.get('beat', '')}")
    edl["total_duration_s"] = round(total, 1)
    args.output.write_text(json.dumps(edl, indent=1, ensure_ascii=False))
    print(f"\n{len(edl['ranges'])} plans, durée totale {int(total // 60)} min {total % 60:04.1f} s → {args.output}")
    for e in errors:
        print("ATTENTION :", e)


if __name__ == "__main__":
    main()
