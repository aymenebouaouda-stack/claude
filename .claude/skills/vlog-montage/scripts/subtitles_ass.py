"""Sous-titres « vlog dynamique » au format ASS (libass), sur la timeline de SORTIE.

Trois styles :
  - Base  : petit, en bas, police grasse, contour ; le mot prononcé est surligné (couleur
            d'accent), 1 ligne de ≤ 26 caractères.
  - Fort  : moments forts (exclamations courtes, mots-clés, plans marqués "emph": true) —
            plus gros, police condensée, majuscules, couleur d'accent, petit effet « pop ».
            Au plus un toutes les `fort_gap` secondes pour garder l'effet spécial.
  - Titre : police spéciale pour les plans marqués "subs_style": "titre" (ex. ouverture).
  - Label : texte explicatif (« Problème avec la voiture », « Arrivée à Paris »…) en haut à
            gauche, dans un cartouche couleur d'accent, posé par un plan marqué "label": "…"
            (durée "label_dur", 3,5 s par défaut), affiché même si le plan n'a pas de sous-titres.
Options de l'EDL (clé "subtitles") : style "dynamic", fontsdir, fixes, min_prob, accent,
keywords (regex), base_size, fort_size, margin_v, label_size, max_chars. Par plan :
"subs_fixes" (corrections propres à ce plan, ex. un mot mal compris à un seul endroit).
Les tailles sont données pour une image de 1080 px de petit côté (vertical 1080x1920 ou
paysage 1920x1080) et mises à l'échelle sinon.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_KEYWORDS = (r"\b(GAV|police|OPJ|félicitations|incroyable|magnifique|palpitant|catastrophe|"
                    r"galère|1\s?300|1\s?200|euros|BG|wallah|machallah|mashallah|bismillah)\b")


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def apply_fixes(text: str, fixes: dict) -> str:
    for bad, good in fixes.items():
        text = re.sub(rf"(?<![\w']){re.escape(bad)}(?![\w'])", good, text, flags=re.IGNORECASE)
    return text


def build_ass(edl: dict, edl_path: Path, durations: list[float], out: Path, W: int, H: int) -> int:
    sub = edl.get("subtitles", {})
    fixes = sub.get("fixes", {})
    min_prob = float(sub.get("min_prob", 0))
    accent = sub.get("accent", "#FFD400")
    kw = re.compile(sub.get("keywords", DEFAULT_KEYWORDS), re.IGNORECASE)
    k = min(W, H) / 1080
    base_size = int(sub.get("base_size", 50) * k)
    fort_size = int(sub.get("fort_size", 96) * k)
    margin_v = int(sub.get("margin_v", 250 if H > W else 70) * k)
    label_size = int(sub.get("label_size", 52) * k)
    max_chars = int(sub.get("max_chars", 26))
    fort_gap = float(sub.get("fort_gap", 6.0))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Vlog Montserrat ExtraBold,{base_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H7A000000,0,0,0,0,100,100,0,0,1,{max(2, int(4*k))},{max(1, int(2*k))},2,60,60,{margin_v},1
Style: Fort,Vlog Anton,{fort_size},{ass_color(accent)},&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,{max(3, int(6*k))},{max(1, int(3*k))},2,60,60,{margin_v},1
Style: Label,Vlog Anton,{label_size},&H00111111,&H00FFFFFF,{ass_color(accent)},&H00000000,0,0,0,0,100,100,1,0,3,{max(6, int(12*k))},0,7,{int(60*k)},60,{int(55*k)},1
Style: Titre,Vlog Anton,{int(fort_size*1.15)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,2,0,1,{max(3, int(6*k))},{max(1, int(3*k))},2,60,60,{int(margin_v*1.4)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events, offset, last_fort, n_cues = [], 0.0, -99.0, 0
    tdir = edl_path.parent / "transcripts"
    hi = ass_color(accent)
    for r, d in zip(edl["ranges"], durations):
        speed = float(r.get("speed", 1.0))
        if r.get("label"):
            ld = float(r.get("label_dur", 3.5))
            events.append(f"Dialogue: 2,{ts(offset + 0.15)},{ts(offset + 0.15 + ld)},Label,,0,0,0,,"
                          f"{{\\fad(180,220)\\move({int(-40*k)},{int(55*k)},{int(60*k)},{int(55*k)},0,220)}}"
                          f"{esc(r['label'])}")
        tfile = tdir / f"{Path(edl['sources'][r['source']]).stem}.json"
        if r.get("mute") or not r.get("subs", True) or not tfile.exists():
            offset += d
            continue
        words = [w for w in json.loads(tfile.read_text())["words"]
                 if w["start"] >= r["start"] - 0.05 and w["end"] <= r["end"] + 0.05]
        # groupes : fin de phrase, pause réelle, ou virgule après 2 mots
        groups, cur = [], []
        for i, w in enumerate(words):
            cur.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            if (nxt is None or re.search(r"[.!?…]$", w["text"]) or nxt["start"] - w["end"] >= 0.35
                    or (w["text"].endswith(",") and len(cur) >= 2)):
                groups.append(cur)
                cur = []
        sentence_start = True
        for g in groups:
            # morceaux de ≤ max_chars caractères
            chunks, c = [], []
            for w in g:
                if c and len(" ".join(x["text"] for x in c + [w])) > max_chars:
                    chunks.append(c)
                    c = []
                c.append(w)
            if c:
                if chunks and len(c) == 1 and len(" ".join(x["text"] for x in chunks[-1] + c)) <= max_chars + 8:
                    chunks[-1] += c  # pas de mot orphelin
                else:
                    chunks.append(c)
            for ci, ch in enumerate(chunks):
                probs = [x["prob"] for x in ch if "prob" in x]
                if probs and sum(probs) / len(probs) < min_prob:
                    continue
                a = max(0.0, (ch[0]["start"] - r["start"]) / speed) + offset
                b = min(d, (ch[-1]["end"] - r["start"]) / speed) + offset
                b = max(b, a + 0.4)
                raw = " ".join(x["text"] for x in ch)
                text = apply_fixes(apply_fixes(raw, r.get("subs_fixes", {})), fixes)
                if ci == 0 and sentence_start and text[:1].islower():
                    text = text[0].upper() + text[1:]
                toks = text.split()
                if not toks:  # passage volontairement masqué (correction vers "")
                    continue
                # temps par mot (si les corrections ont changé le nombre de mots, répartition au prorata)
                if len(toks) == len(ch):
                    starts = [max(0.0, (x["start"] - r["start"]) / speed) + offset for x in ch]
                else:
                    starts = [a + (b - a) * j / len(toks) for j in range(len(toks))]
                style = r.get("subs_style", "")
                is_fort = (style != "titre" and len(toks) <= 4 and (
                    r.get("emph") or (text.endswith("!") and len(toks) <= 3) or kw.search(text))
                    and a - last_fort >= fort_gap) or (r.get("emph") and style != "titre")
                n_cues += 1
                if style == "titre":
                    events.append(f"Dialogue: 0,{ts(a)},{ts(b)},Titre,,0,0,0,,{{\\fad(120,80)}}{esc(text.upper())}")
                elif is_fort:
                    last_fort = a
                    pop = "{\\fscx70\\fscy70\\t(0,110,\\fscx112\\fscy112)\\t(110,200,\\fscx100\\fscy100)}"
                    events.append(f"Dialogue: 1,{ts(a)},{ts(b)},Fort,,0,0,0,,{pop}{esc(text.upper())}")
                else:
                    for j in range(len(toks)):
                        s0 = starts[j] if j else a
                        e0 = starts[j + 1] if j + 1 < len(toks) else b
                        if e0 - s0 < 0.02:
                            continue
                        parts = [(f"{{\\c{hi}}}{esc(t)}{{\\c&H00FFFFFF&}}" if jj == j else esc(t))
                                 for jj, t in enumerate(toks)]
                        events.append(f"Dialogue: 0,{ts(s0)},{ts(e0)},Base,,0,0,0,,{' '.join(parts)}")
            sentence_start = bool(re.search(r"[.!?…]$", g[-1]["text"]))
        offset += d
    out.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return n_cues
