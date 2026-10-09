"""Rendu d'un vlog à partir d'une EDL (liste de plans) JSON.

Ordre du pipeline (chaque étape a une raison) :
  1. Extraction plan par plan, ré-encodée au format de sortie (cadrage, fps, étalonnage)
     avec un fondu audio de 30 ms à chaque bord → pas de « clic » aux coupes.
  2. Concaténation sans ré-encodage (-c copy) → une seule génération de compression.
  3. Passe finale : musique (ducking sous la voix) puis sous-titres EN DERNIER
     (sinon un élément superposé les masque), puis normalisation -14 LUFS / -1 dBTP.

EDL :
{
  "output": {"width": 1080, "height": 1920, "fps": 30, "fit": "blur"},   # fit: fill | blur | pad
  "grade": "eq=contrast=1.05:saturation=1.08",                            # optionnel, filtre ffmpeg
  "sources": {"A": "/abs/rush1.mp4", "B": "/abs/rush2.mp4"},
  "ranges": [
    {"source": "A", "start": 12.40, "end": 15.10, "beat": "HOOK", "note": "..."},
    {"source": "B", "start": 3.00, "end": 6.00, "beat": "BROLL", "mute": true, "speed": 1.0}
  ],
  "music": {"file": "/abs/musique.mp3", "volume_db": -20, "duck": true},  # optionnel
  "subtitles": {"from_transcripts": true, "style": "bold"}                  # optionnel
}

Usage:
    python3 render.py <edl.json> -o <edit_dir>/final.mp4 [--preview]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

FADE = 0.03
SUB_STYLES = {
    # Format court/vertical : 2-3 mots, majuscules, gras, contour noir, au-dessus de l'UI TikTok/Reels.
    "bold": ("FontName=DejaVu Sans,FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90", 3, True),
    # Format long/YouTube : phrases courtes en casse normale, bas d'écran.
    "natural": ("FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2,MarginV=30", 7, False),
}


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"échec : {' '.join(cmd[:6])}…\n{p.stderr[-2000:]}")
    return p.stderr


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(out.stdout.strip())


def fit_filter(w: int, h: int, fit: str) -> str:
    if fit == "fill":  # recadrage plein cadre (centre)
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if fit == "pad":  # bandes noires
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    # blur : image entière au centre sur un fond flouté de la même image
    return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=20:2[bgb];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")


def extract(edl: dict, i: int, r: dict, out: Path, preview: bool) -> float:
    o = edl["output"]
    w, h, fps = o["width"], o["height"], o.get("fps", 30)
    if preview:
        w, h = w // 2 // 2 * 2, h // 2 // 2 * 2
    src = edl["sources"][r["source"]]
    speed = float(r.get("speed", 1.0))
    dur = (r["end"] - r["start"]) / speed
    vf = fit_filter(w, h, o.get("fit", "blur"))
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
    cmd += ["-filter_complex", f"[0:v]{vf}[v];[{amap}]{af},aresample=48000,"
                               f"aformat=channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast" if preview else "medium",
            "-crf", "23" if preview else "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            # même base de temps pour tous les plans → concaténation propre
            "-video_track_timescale", str(int(round(fps * 1000))), "-y", str(out)]
    run(cmd)
    return dur


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build_srt(edl: dict, edl_path: Path, durations: list[float], style: str, out: Path) -> int:
    """Sous-titres sur la timeline de SORTIE : t_sortie = mot.start - plan.start + décalage_du_plan."""
    tdir = edl_path.parent / "transcripts"
    _, max_words, upper = SUB_STYLES[style]
    cues, offset = [], 0.0
    for r, d in zip(edl["ranges"], durations):
        speed = float(r.get("speed", 1.0))
        tfile = tdir / f"{Path(edl['sources'][r['source']]).stem}.json"
        if not r.get("mute") and tfile.exists():
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
                    a = max(0.0, (chunk[0]["start"] - r["start"]) / speed) + offset
                    b = min(d, (chunk[-1]["end"] - r["start"]) / speed) + offset
                    txt = " ".join(x["text"] for x in chunk)
                    cues.append((a, max(b, a + 0.35), txt.upper() if upper else txt))
        offset += d
    # éviter les chevauchements créés par la durée minimale
    for k in range(len(cues) - 1):
        if cues[k][1] > cues[k + 1][0]:
            cues[k] = (cues[k][0], cues[k + 1][0], cues[k][2])
    out.write_text("\n".join(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n"
                             for n, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")
    return len(cues)


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
        parts, durs = [], []
        for i, r in enumerate(edl["ranges"]):
            p = tmp / f"seg{i:04d}.mp4"
            durs.append(extract(edl, i, r, p, args.preview))
            parts.append(p)
            print(f"  plan {i + 1}/{len(edl['ranges'])} {r['source']} {r['start']:.2f}-{r['end']:.2f} "
                  f"({r.get('beat', '')})")
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

        # ---- vidéo : sous-titres EN DERNIER ----
        vchain = ""
        subs = edl.get("subtitles")
        if subs:
            style = subs.get("style", "bold")
            srt = args.output.with_suffix(".srt")
            if subs.get("file"):
                srt = Path(subs["file"])
            elif subs.get("from_transcripts", True):
                n = build_srt(edl, args.edl, durs, style, srt)
                print(f"  sous-titres : {n} répliques → {srt}")
            esc = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            vchain = f"[0:v]subtitles='{esc}':force_style='{SUB_STYLES[style][0]}'[v]"

        # Sans sous-titres, la vidéo est copiée telle quelle (pas de 2e compression).
        vcodec = (["-c:v", "libx264", "-preset", "veryfast" if args.preview else "medium",
                   "-crf", "23" if args.preview else "18", "-pix_fmt", "yuv420p"]
                  if subs else ["-c:v", "copy"])
        vmap = ["-map", "[v]"] if subs else ["-map", "0:v"]
        fc = f"{vchain};" if subs else ""
        run(["ffmpeg", "-v", "error", "-i", str(base), *extra, "-filter_complex",
             f"{fc}{fc_audio}[mix];[mix]{loud}[a]", *vmap, "-map", "[a]", *vcodec,
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}",
             "-y", str(args.output)])
    print(f"OK → {args.output} ({total:.1f}s, {len(parts)} plans)")


if __name__ == "__main__":
    main()
