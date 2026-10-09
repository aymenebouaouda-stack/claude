"""Rendu d'un vlog à partir d'une EDL (liste de plans) JSON.

Ordre du pipeline (chaque étape a une raison) :
  1. Extraction plan par plan, ré-encodée au format de sortie (cadrage, fps, étalonnage)
     avec un fondu audio de 30 ms à chaque bord → pas de « clic » aux coupes.
  2. Concaténation sans ré-encodage (-c copy) → une seule génération de compression.
  3. Passe finale : musique (ducking sous la voix), titres incrustés, puis sous-titres
     EN DERNIER (sinon un élément superposé les masque), normalisation -14 LUFS / -1 dBTP.

EDL :
{
  "output": {"width": 1080, "height": 1920, "fps": 30, "fit": "blur"},   # fit: fill | blur | pad | band
  # blurzoom : image agrandie (hauteur "fg_height", défaut 0.42) côtés recadrés, fond flouté
  # band : bandeau face caméra sur fond noir (style TikTok « tuto ») ; réglages optionnels
  #        "band_top": 0.20, "band_height": 0.42 (fractions de la hauteur)
  "grade": "eq=contrast=1.05:saturation=1.08",                            # optionnel, filtre ffmpeg
  "sources": {"A": "/abs/rush1.mp4", "B": "/abs/rush2.mp4"},
  "ranges": [
    {"source": "A", "start": 12.40, "end": 15.10, "beat": "HOOK", "note": "..."},
    {"source": "B", "start": 3.00, "end": 6.00, "beat": "BROLL", "mute": true, "speed": 1.0,
     "rotate": 90}                                       # rotate : 90 | 180 | 270 (sens horaire)
  ],
  "music": {"file": "/abs/musique.mp3", "volume_db": -20, "duck": true},  # optionnel
  "titles": [                                                               # optionnel
    {"text": "LES TROIS\nOUTILS", "start": 14.8, "end": 16.4, "y": 0.10, "size": 0.12,
     "font": "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", "condense": 0.75}
  ],
  "subtitles": {"from_transcripts": true, "style": "bold",                 # bold | natural | serif | vlog
                "fixes": {"nazi": "Nazim"}},                              # corrections mot à mot (regex \b)
  "normalize_audio": -18,      # optionnel : chaque plan ramené à ~-18 LUFS (gain mesuré, borné)
  "jobs": 2                    # optionnel : plans extraits en parallèle
}
Options par plan : "fit" (remplace celui de output), "filter" (filtre vidéo ffmpeg appliqué
avant le cadrage, ex. éclaircir : "eq=brightness=0.06:gamma=1.3"), "gain_db" (gain manuel),
"subs": false (pas de sous-titres sur ce plan, ex. paroles de chanson).
Sous-titres : "min_prob" (ex. 0.45) saute les répliques dont la confiance Whisper moyenne est faible.
Les plans extraits sont mis en cache (edit/cache_segments) : un re-rendu après retouche ne
ré-encode que les plans modifiés.

Usage:
    python3 render.py <edl.json> -o <edit_dir>/final.mp4 [--preview]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FADE = 0.03
SUB_STYLES = {
    # Format court/vertical : 2-3 mots, majuscules, gras, contour noir, au-dessus de l'UI TikTok/Reels.
    "bold": ("FontName=DejaVu Sans,FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90", 3, True),
    # Format long/YouTube : phrases courtes en casse normale, bas d'écran.
    "natural": ("FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginV=30", 7, False),
    # Tuto/éditorial (vu dans la vidéo de référence) : serif, casse normale, 2-3 mots,
    # posé sous le bandeau face caméra (fit "band").
    "serif": ("FontName=Liberation Serif,FontSize=15,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
              "BorderStyle=1,Outline=0.6,Shadow=0,Alignment=2,MarginV=60", 3, False),
    # Vlog long format portrait : casse normale, gras, contour + ombre, 4 mots max, tiers bas.
    "vlog": ("FontName=DejaVu Sans,FontSize=12,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BackColour=&H80000000,BorderStyle=1,Outline=1.6,Shadow=0.8,Alignment=2,MarginV=70", 4, False),
}
TITLE_FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"échec : {' '.join(cmd[:6])}…\n{p.stderr[-2000:]}")
    return p.stderr


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(out.stdout.strip())


def fit_filter(w: int, h: int, fit: str, o: dict | None = None) -> str:
    o = o or {}
    if fit == "band":  # bandeau plein largeur sur fond noir
        bh = int(h * o.get("band_height", 0.42)) // 2 * 2
        top = int(h * o.get("band_top", 0.20)) // 2 * 2
        return (f"scale={w}:{bh}:force_original_aspect_ratio=increase,crop={w}:{bh},"
                f"pad={w}:{h}:0:{top}:black")
    if fit == "blurzoom":  # image agrandie (hauteur = fg_height × H), côtés recadrés, sur fond flouté
        fh = int(h * float(o.get("fg_height", 0.42))) // 2 * 2
        return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=20:2[bgb];[fg]scale=-2:{fh},crop='min(iw,{w})':{fh}[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    if fit == "fill":  # recadrage plein cadre (centre)
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "pad":  # bandes noires
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    # blur : image entière au centre sur un fond flouté de la même image
    return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=20:2[bgb];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")


def range_loudness(src: str, start: float, end: float) -> float | None:
    """Loudness intégrée (LUFS) d'une plage de la source, ou None si silence/mesure impossible."""
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                          "-i", src, "-vn", "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    if not m or float(m[-1]) < -60:
        return None
    return float(m[-1])


def extract(edl: dict, i: int, r: dict, out: Path, preview: bool) -> float:
    o = edl["output"]
    w, h, fps = o["width"], o["height"], o.get("fps", 30)
    if preview:
        w, h = w // 2 // 2 * 2, h // 2 // 2 * 2
    src = edl["sources"][r["source"]]
    speed = float(r.get("speed", 1.0))
    dur = (r["end"] - r["start"]) / speed
    vf = fit_filter(w, h, r.get("fit", o.get("fit", "blur")), o)
    if r.get("filter"):
        vf = r["filter"] + "," + vf
    # Redressement d'un plan filmé de travers (rotation du CONTENU, en degrés horaires)
    rot = int(r.get("rotate", 0)) % 360
    if rot:
        vf = {90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}[rot] + "," + vf
    if speed != 1.0:
        vf = f"setpts=PTS/{speed}," + vf
    if edl.get("grade"):
        vf += "," + edl["grade"]
    vf += f",fps={fps},format=yuv420p,setsar=1"

    af = f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE}"
    if speed != 1.0:
        af = f"atempo={speed}," + af
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{r['start']:.3f}", "-to", f"{r['end']:.3f}", "-i", src]
    if r.get("mute") or not has_audio(src):
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a"
        af = "anull"
    else:
        amap = "0:a:0"
        gain = r.get("gain_db")
        if gain is None and edl.get("normalize_audio") is not None:
            lufs = range_loudness(src, r["start"], r["end"])
            if lufs is not None:
                gain = max(-12.0, min(15.0, float(edl["normalize_audio"]) - lufs))
        if gain:
            af = f"volume={gain:.2f}dB,alimiter=limit=0.95:level=false," + af
    cmd += ["-filter_complex", f"[0:v]{vf}[v];[{amap}]{af},aresample=48000,"
                               f"aformat=channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast" if preview else o.get("preset", "medium"),
            "-crf", "23" if preview else str(o.get("crf", 18)), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            # même base de temps pour tous les plans → concaténation propre
            "-video_track_timescale", str(int(round(fps * 1000))), "-y", str(out)]
    run(cmd)
    return dur


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build_srt(edl: dict, edl_path: Path, durations: list[float], style: str, out: Path,
              fixes: dict | None = None, min_prob: float = 0.0) -> int:
    """Sous-titres sur la timeline de SORTIE : t_sortie = mot.start - plan.start + décalage_du_plan."""
    tdir = edl_path.parent / "transcripts"
    _, max_words, upper = SUB_STYLES[style]
    cues, offset = [], 0.0
    for r, d in zip(edl["ranges"], durations):
        speed = float(r.get("speed", 1.0))
        tfile = tdir / f"{Path(edl['sources'][r['source']]).stem}.json"
        if not r.get("mute") and r.get("subs", True) and tfile.exists():
            words = [w for w in json.loads(tfile.read_text())["words"]
                     if w["start"] >= r["start"] - 0.05 and w["end"] <= r["end"] + 0.05]
            # 1) groupes naturels : coupure sur ponctuation ou pause ≥ 0.3 s
            groups, cur = [], []
            for k, w in enumerate(words):
                cur.append(w)
                nxt = words[k + 1] if k + 1 < len(words) else None
                if nxt is None or re.search(r"[.,!?…]$", w["text"]) or nxt["start"] - w["end"] >= 0.3:
                    groups.append(cur)
                    cur = []
            # 2) chaque groupe découpé en morceaux équilibrés (pas de mot orphelin)
            for g in groups:
                n = -(-len(g) // max_words)
                size = -(-len(g) // n)
                for j in range(0, len(g), size):
                    chunk = g[j:j + size]
                    first_of_group = j == 0
                    probs = [x["prob"] for x in chunk if "prob" in x]
                    if probs and sum(probs) / len(probs) < min_prob:
                        continue  # transcription trop incertaine : mieux vaut pas de sous-titre qu'un faux
                    a = max(0.0, (chunk[0]["start"] - r["start"]) / speed) + offset
                    b = min(d, (chunk[-1]["end"] - r["start"]) / speed) + offset
                    txt = " ".join(x["text"] for x in chunk)
                    if first_of_group and txt[:1].islower() and not upper:
                        txt = txt[0].upper() + txt[1:]
                    for bad, good in (fixes or {}).items():
                        txt = re.sub(rf"\b{bad}\b", good, txt, flags=re.IGNORECASE)
                    cues.append((a, max(b, a + 0.35), txt.upper() if upper else txt))
        offset += d
    # éviter les chevauchements créés par la durée minimale
    for k in range(len(cues) - 1):
        if cues[k][1] > cues[k + 1][0]:
            cues[k] = (cues[k][0], cues[k + 1][0], cues[k][2])
    out.write_text("\n".join(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n"
                             for n, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")
    return len(cues)


def title_png(t: dict, w: int, h: int, out: Path) -> None:
    """Titre plein cadre transparent : grosses capitales serif « condensées » (étirement horizontal)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(t.get("font", TITLE_FONT), max(8, int(h * t.get("size", 0.10))))
    lines = t["text"].split("\n")
    condense = float(t.get("condense", 0.75))
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = int(h * t.get("y", 0.10))
    for line in lines:
        l, tp, r, b = font.getbbox(line)
        tw, th = r - l, b - tp
        img = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((4 - l, 4 - tp), line, font=font, fill=t.get("color", "#FFFFFF"))
        img = img.resize((max(1, int(img.width * condense)), img.height), Image.LANCZOS)
        if img.width > w * 0.94:  # ne jamais déborder du cadre
            k = w * 0.94 / img.width
            img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
        x = int(w * t["x"]) if "x" in t else (w - img.width) // 2
        canvas.alpha_composite(img, (x, y))
        y += int(img.height * 0.92)
    canvas.save(out)


def loudnorm_params(path: Path, extra_inputs: list[str], fc_audio: str) -> dict:
    log = run(["ffmpeg", "-hide_banner", "-i", str(path), *extra_inputs, "-filter_complex",
               f"{fc_audio}[mix];[mix]loudnorm=I=-14:TP=-1:LRA=11:print_format=json[o]",
               "-map", "[o]", "-f", "null", "-"])
    return json.loads(log[log.rindex("{"):log.rindex("}") + 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("edl", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--preview", action="store_true", help="Demi-résolution, encodage rapide")
    args = ap.parse_args()

    edl = json.loads(args.edl.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cache = args.edl.parent / "cache_segments"
        cache.mkdir(exist_ok=True)
        common = {k: edl.get(k) for k in ("output", "grade", "normalize_audio")}

        def one(i: int, r: dict) -> tuple[Path, float]:
            key = hashlib.sha1(json.dumps([r, common, edl["sources"][r["source"]], args.preview, 2],
                                          sort_keys=True).encode()).hexdigest()[:16]
            p = cache / f"{key}.mp4"
            speed = float(r.get("speed", 1.0))
            if not p.exists():
                part = p.with_suffix(".part.mp4")
                extract(edl, i, r, part, args.preview)
                part.rename(p)
                print(f"  plan {i + 1}/{len(edl['ranges'])} {r['source']} {r['start']:.2f}-{r['end']:.2f} "
                      f"({r.get('beat', '')})", flush=True)
            return p, (r["end"] - r["start"]) / speed

        with ThreadPoolExecutor(int(edl.get("jobs", 2))) as ex:
            results = list(ex.map(lambda ir: one(*ir), enumerate(edl["ranges"])))
        parts = [p for p, _ in results]
        durs = [d for _, d in results]
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        base = tmp / "base.mp4"
        run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
             "-y", str(base)])
        total = sum(durs)

        # ---- audio : voix (+ musique avec ducking) ----
        extra, fc_audio = [], "[0:a]anull"
        m = edl.get("music")
        if m:
            extra = ["-stream_loop", "-1", "-i", m["file"]]
            vol = m.get("volume_db", -20)
            mus = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,"
                   f"atrim=0:{total:.3f},afade=t=in:d=1,afade=t=out:st={max(0, total - 2):.3f}:d=2")
            if m.get("duck", True):
                fc_audio = (f"[0:a]asplit=2[voice][sc];{mus}[mus];"
                            "[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
                            "[voice][duck]amix=inputs=2:duration=first:normalize=0")
            else:
                fc_audio = f"{mus}[mus];[0:a][mus]amix=inputs=2:duration=first:normalize=0"
        ln = loudnorm_params(base, extra, fc_audio)
        loud = (f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={ln['input_i']}:measured_TP={ln['input_tp']}:"
                f"measured_LRA={ln['input_lra']}:measured_thresh={ln['input_thresh']}:"
                f"offset={ln['target_offset']}:linear=true,aresample=48000")

        # ---- vidéo : titres incrustés, puis sous-titres EN DERNIER ----
        out_w, out_h = (int(x) for x in subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(base)], capture_output=True, text=True).stdout.strip().split(","))
        fps = edl["output"].get("fps", 30)
        vparts, cur = [], "0:v"
        for k, t in enumerate(edl.get("titles", [])):
            png = tmp / f"title{k}.png"
            title_png(t, out_w, out_h, png)
            idx = 1 + (1 if m else 0) + k
            extra += ["-loop", "1", "-framerate", str(fps), "-t", f"{total:.3f}", "-i", str(png)]
            a, b = float(t["start"]), float(t["end"])
            fd = min(0.12, (b - a) / 3)
            vparts.append(f"[{idx}:v]format=rgba,fade=t=in:st={a:.3f}:d={fd:.3f}:alpha=1,"
                          f"fade=t=out:st={b - fd:.3f}:d={fd:.3f}:alpha=1[t{k}];"
                          f"[{cur}][t{k}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[o{k}]")
            cur = f"o{k}"
        subs = edl.get("subtitles")
        if subs:
            style = subs.get("style", "bold")
            srt = args.output.with_suffix(".srt")
            if subs.get("file"):
                srt = Path(subs["file"])
            elif subs.get("from_transcripts", True):
                n = build_srt(edl, args.edl, durs, style, srt, subs.get("fixes"), float(subs.get("min_prob", 0)))
                print(f"  sous-titres : {n} répliques → {srt}")
            esc = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            vparts.append(f"[{cur}]subtitles='{esc}':force_style='{SUB_STYLES[style][0]}'[sub]")
            cur = "sub"

        # Sans titres ni sous-titres, la vidéo est copiée telle quelle (pas de 2e compression).
        touched = bool(vparts)
        vcodec = (["-c:v", "libx264", "-preset", "veryfast" if args.preview else "medium",
                   "-crf", "23" if args.preview else "18", "-pix_fmt", "yuv420p"]
                  if touched else ["-c:v", "copy"])
        vmap = ["-map", f"[{cur}]"] if touched else ["-map", "0:v"]
        fc = ";".join(vparts) + ";" if touched else ""
        run(["ffmpeg", "-v", "error", "-i", str(base), *extra, "-filter_complex",
             f"{fc}{fc_audio}[mix];[mix]{loud}[a]", *vmap, "-map", "[a]", *vcodec,
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}",
             "-y", str(args.output)])
    print(f"OK → {args.output} ({total:.1f}s, {len(parts)} plans)")


if __name__ == "__main__":
    main()
