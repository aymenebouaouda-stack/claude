"""Transcription Gemini par LOTS : plusieurs rushes dans une seule requête (palier gratuit limité
en nombre de requêtes par jour, p. ex. 20/jour/modèle constaté le 2026-10-09).

Chaque lot = audio des rushes mis bout à bout, séparés par un bip de 1 kHz. Le modèle doit écrire
« <BIP> » à chaque bip : les bips détectés servent à recaler les horodatages (correction linéaire
par morceaux), puis chaque segment est rendu à son rush avec un temps LOCAL.

Sortie : identique à transcribe_gemini.py (<edit>/transcripts/<nom>.json + .txt), champ
"batch": true. Les rushes déjà transcrits sont ignorés (cache).

Usage:
    python3 transcribe_gemini_batch.py <video1> <video2> … --edit-dir <edit> [--max-min 8]
           [--model gemini-3.8-flash] [--fallback gemini-2.5-flash] [--context "…"]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe_gemini import API, SCHEMA, Busy, api_key, call, upload  # noqa: E402

SR = 16000
GAP_BEFORE, BEEP, GAP_AFTER = 0.8, 0.5, 0.7


def duration(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True).stdout.strip() or 0)


def build_batch(videos: list[Path], tmp: Path) -> tuple[Path, list[dict]]:
    parts, layout, t = [], [], 0.0
    sep = tmp / "sep.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"anullsrc=r={SR}:cl=mono:d={GAP_BEFORE}", "-f", "lavfi", "-i",
                    f"sine=f=1000:r={SR}:d={BEEP}", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono:d={GAP_AFTER}",
                    "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1", "-y", str(sep)], check=True)
    for i, v in enumerate(videos):
        w = tmp / f"a{i:03d}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(v), "-vn", "-ac", "1", "-ar", str(SR),
                        "-af", "loudnorm=I=-20", "-y", str(w)], check=True)
        d = duration(w)
        layout.append({"video": v, "beep": t + GAP_BEFORE, "start": t + GAP_BEFORE + BEEP + GAP_AFTER,
                       "dur": d})
        parts += [sep, w]
        t += GAP_BEFORE + BEEP + GAP_AFTER + d
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = tmp / "batch.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "aac",
                    "-b:a", "48k", "-y", str(out)], check=True)
    return out, layout


def correct(t: float, model_beeps: list[float], true_beeps: list[float]) -> float:
    """Recalage linéaire par morceaux entre bips (si autant de bips trouvés que posés)."""
    if len(model_beeps) != len(true_beeps) or not model_beeps:
        return t
    pts = list(zip(model_beeps, true_beeps))
    for (m0, r0), (m1, r1) in zip(pts, pts[1:]):
        if m0 <= t <= m1 and m1 > m0:
            return r0 + (t - m0) * (r1 - r0) / (m1 - m0)
    m, r = pts[-1] if t > pts[-1][0] else pts[0]
    return r + (t - m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--max-min", type=float, default=8.0, help="Durée audio max par lot (min)")
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--fallback", nargs="*", default=["gemini-2.5-flash", "gemini-3.5-flash"])
    ap.add_argument("--language", default="fr")
    ap.add_argument("--context", default="")
    args = ap.parse_args()

    out_dir = args.edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [v for v in args.videos if not (out_dir / f"{v.stem}.json").exists()]
    batches, cur, cur_d = [], [], 0.0
    for v in todo:
        d = duration(v)
        if cur and cur_d + d > args.max_min * 60:
            batches.append(cur)
            cur, cur_d = [], 0.0
        cur.append(v)
        cur_d += d
    if cur:
        batches.append(cur)
    print(f"{len(todo)} rushes à transcrire en {len(batches)} requête(s)")
    key = api_key()

    for bi, batch in enumerate(batches, 1):
        with tempfile.TemporaryDirectory() as tmp:
            audio, layout = build_batch(batch, Path(tmp))
            f = upload(audio, key, "audio/mp4")
            try:
                prompt = (
                    f"Cet audio contient {len(batch)} extraits différents, chacun précédé d'un BIP aigu. "
                    "Pour CHAQUE bip, écris un segment dont le texte est exactement <BIP> avec son heure. "
                    f"Transcris mot pour mot TOUT ce qui est dit (langue principale : {args.language}), "
                    "y compris hésitations, argot, rires entre parenthèses. Segments courts (6 s max), "
                    "début et fin en SECONDES depuis le début du fichier (décimales). N'invente rien ; "
                    "inaudible → [inaudible]. Ignore la musique sauf si quelqu'un chante. "
                    "speaker = « A », « B »… par voix (les lettres peuvent changer d'un extrait à l'autre). "
                    + (f"Orthographe des noms propres : {args.context}." if args.context else ""))
                body = {"contents": [{"parts": [{"file_data": {"mime_type": f["mimeType"], "file_uri": f["uri"]}},
                                                {"text": prompt}]}],
                        "generationConfig": {"temperature": 0, "response_mime_type": "application/json",
                                             "response_schema": SCHEMA}}
                raw, used = None, None
                for model in [args.model, *args.fallback]:
                    try:
                        _, raw = call("POST", f"{API}/v1beta/models/{model}:generateContent", key,
                                      json.dumps(body).encode(), {"Content-Type": "application/json"}, tries=3)
                        used = model
                        break
                    except Busy as e:
                        print(f"  {model} indisponible ({str(e)[:120]}) → suivant")
                if raw is None:
                    raise SystemExit(f"lot {bi} : tous les modèles sont indisponibles/épuisés ; réessayer plus tard")
            finally:
                try:
                    call("DELETE", f"{API}/v1beta/{f['name']}", key, tries=2)
                except Busy:
                    pass

        resp = json.loads(raw)
        segs = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])["segments"]
        model_beeps = [float(s["start"]) for s in segs if "BIP" in s["text"].upper() and len(s["text"]) < 12]
        true_beeps = [c["beep"] for c in layout]
        ok = len(model_beeps) == len(true_beeps)
        per = {id(c): [] for c in layout}
        for s in segs:
            if "BIP" in s["text"].upper() and len(s["text"]) < 12:
                continue
            a = correct(float(s["start"]), model_beeps, true_beeps)
            b = correct(float(s["end"]), model_beeps, true_beeps)
            owner = max((c for c in layout if c["start"] - 1.0 <= a), key=lambda c: c["start"], default=layout[0])
            la, lb = max(0.0, a - owner["start"]), min(owner["dur"], max(0.0, b - owner["start"]))
            if la >= owner["dur"]:
                continue
            per[id(owner)].append({"start": round(la, 2), "end": round(max(lb, la + 0.3), 2),
                                   "speaker": s.get("speaker"), "text": s["text"]})
        for c in layout:
            words = []
            for seg in per[id(c)]:
                toks = seg["text"].split()
                tot = sum(len(t) for t in toks) or 1
                t = seg["start"]
                for tok in toks:
                    d = (seg["end"] - seg["start"]) * len(tok) / tot
                    words.append({"type": "word", "text": tok, "start": round(t, 3), "end": round(t + d, 3),
                                  "speaker_id": seg.get("speaker"), "estimated": True})
                    t += d
            stem = c["video"].stem
            (out_dir / f"{stem}.json").write_text(json.dumps(
                {"source": str(c["video"].resolve()), "engine": "gemini", "model": used, "batch": True,
                 "beeps_aligned": ok, "language": args.language, "segments": per[id(c)], "words": words},
                ensure_ascii=False, indent=1))
            (out_dir / f"{stem}.txt").write_text("\n".join(
                f"[{s['start']:07.2f}-{s['end']:07.2f}] {s.get('speaker') or ''} {s['text']}" for s in per[id(c)]),
                encoding="utf-8")
        print(f"lot {bi}/{len(batches)} : {len(batch)} rushes, modèle {used}, bips {len(model_beeps)}/"
              f"{len(true_beeps)} {'(recalé)' if ok else '(NON recalé)'}, "
              f"tokens {resp.get('usageMetadata', {}).get('totalTokenCount', '?')}")


if __name__ == "__main__":
    main()
