"""Repère les coupes qui « coupent la parole » dans une EDL (fin ou début de plan au milieu d'une phrase).

Pour chaque bord de plan, regarde le mot prononcé juste après la fin (ou juste avant le début)
dans la transcription du rush :
  - FIN   suspecte : un mot suit à moins de `--gap` s et le dernier mot gardé ne termine pas une
    phrase (. ! ? …) ;
  - DÉBUT suspect : un mot précède à moins de `--gap` s et ne termine pas une phrase.
Les bords internes à un « tighten » (blanc ≥ min_gap) ne sont pas signalés. La ponctuation de
Whisper étant imparfaite, chaque alerte est à écouter : l'outil liste, l'humain (ou l'agent) tranche.

Usage:
    python3 speech_cuts.py edl.json [--gap 0.6] [--context 8]
Affiche le timecode de SORTIE de chaque alerte, le texte autour de la coupe et la suite coupée.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

END = re.compile(r"[.!?…]$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("--gap", type=float, default=0.6)
    ap.add_argument("--context", type=int, default=8)
    args = ap.parse_args()
    edl = json.loads(args.edl.read_text())
    fps = int(edl["output"].get("fps", 30))
    tdir = args.edl.parent / "transcripts"
    t, n = 0.0, 0
    ranges = edl["ranges"]
    for k, r in enumerate(ranges):
        d = round((r["end"] - r["start"]) / float(r.get("speed", 1.0)) * fps) / fps
        tf = tdir / f"{Path(edl['sources'][r['source']]).stem}.json"
        if tf.exists() and not r.get("mute"):
            words = json.loads(tf.read_text())["words"]
            nxt_r = ranges[k + 1] if k + 1 < len(ranges) else None
            prv_r = ranges[k - 1] if k else None
            inside = [w for w in words if w["start"] >= r["start"] - 0.05 and w["end"] <= r["end"] + 0.05]
            after = [w for w in words if w["start"] > r["end"] - 0.05 and w not in inside]
            before = [w for w in words if w["end"] < r["start"] + 0.05 and w not in inside]
            # fin : la suite immédiate n'est pas le plan suivant (sinon c'est un simple jump cut)
            cont = nxt_r and nxt_r["source"] == r["source"] and abs(nxt_r["start"] - r["end"]) < 1.5
            if inside and after and not cont and after[0]["start"] - inside[-1]["end"] < args.gap \
                    and not END.search(inside[-1]["text"]):
                n += 1
                print(f"[FIN   {int((t + d) // 60)}:{(t + d) % 60:05.2f}] {r['source']} @{r['end']:.2f}s  "
                      f"…{' '.join(w['text'] for w in inside[-args.context:])}  ‖ coupé : "
                      f"{' '.join(w['text'] for w in after[:args.context])}…")
            cont = prv_r and prv_r["source"] == r["source"] and abs(r["start"] - prv_r["end"]) < 1.5
            if inside and before and not cont and inside[0]["start"] - before[-1]["end"] < args.gap \
                    and not END.search(before[-1]["text"]):
                n += 1
                print(f"[DÉBUT {int(t // 60)}:{t % 60:05.2f}] {r['source']} @{r['start']:.2f}s  coupé : "
                      f"…{' '.join(w['text'] for w in before[-args.context:])}  ‖ "
                      f"{' '.join(w['text'] for w in inside[:args.context])}…")
        t += d
    print(f"\n{n} coupe(s) à écouter")


if __name__ == "__main__":
    main()
