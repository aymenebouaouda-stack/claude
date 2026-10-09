"""Transcription locale (faster-whisper, CPU) avec horodatage au mot, mise en cache.

Sortie : <edit_dir>/transcripts/<nom_du_rush>.json
    {"source": ..., "language": "fr", "words": [{"type": "word", "text": "...", "start": s, "end": s}, ...]}
et un résumé lisible <edit_dir>/transcripts/<nom_du_rush>.txt (phrases coupées sur les pauses ≥ 0.5 s).

Limite connue : Whisper a tendance à supprimer les hésitations (« euh », « hum »).
Pour les repérer, croiser avec silences.py et vérifier à l'image.

Prérequis : `pip install faster-whisper` et accès réseau à huggingface.co au premier
lancement (téléchargement du modèle), ou --model <chemin_local_du_modèle>.

Usage:
    python3 transcribe_local.py <video> [<video>…] --edit-dir <edit_dir> [--model small] [--language fr]
           [--prompt "Prénom1, Prénom2, Lieu"]
Le modèle est chargé une seule fois pour tous les rushes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path


def phrases(words: list[dict], gap: float = 0.5) -> list[tuple[float, float, str]]:
    out, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] >= gap:
            out.append((cur[0]["start"], cur[-1]["end"], " ".join(x["text"] for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0]["start"], cur[-1]["end"], " ".join(x["text"] for x in cur)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", type=Path, nargs="+")
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--model", default="small",
                    help="tiny/base/small/medium/large-v3/large-v3-turbo ou chemin local")
    ap.add_argument("--language", default=None, help="ex. fr ; auto-détection si absent")
    ap.add_argument("--prompt", default="", help="Noms propres/lieux pour l'orthographe")
    ap.add_argument("--threads", type=int, default=0, help="Threads CPU (0 = auto)")
    ap.add_argument("--force", action="store_true", help="Ignorer le cache")
    args = ap.parse_args()

    out_dir = args.edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    def cached(v: Path) -> bool:
        f = out_dir / f"{v.stem}.json"
        if not f.exists() or args.force:
            return False
        try:  # une transcription d'un autre moteur (Gemini) est remplacée par Whisper
            return json.loads(f.read_text()).get("engine") == "whisper"
        except json.JSONDecodeError:
            return False

    todo = [v for v in args.videos if not cached(v)]
    print(f"{len(todo)} rush(es) à transcrire ({len(args.videos) - len(todo)} en cache)", flush=True)
    if not todo:
        return

    from faster_whisper import WhisperModel  # import tardif : seul ce script en dépend

    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    prompt = "Euh, alors, hum, en fait, du coup… Voilà." + (f" {args.prompt}" if args.prompt else "")
    for n, video in enumerate(todo, 1):
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                            "-ar", "16000", "-y", str(wav)], check=True)
            segments, info = model.transcribe(
                str(wav), language=args.language, word_timestamps=True,
                # VAD intégré (silero, fourni avec faster-whisper) : limite les hallucinations
                # sur la musique et les bruits ; pas de conditionnement → moins de boucles.
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                # Invite à garder les hésitations ; sans garantie, Whisper peut les omettre.
                initial_prompt=prompt,
            )
            words = []
            for seg in segments:
                for w in seg.words or []:
                    words.append({"type": "word", "text": w.word.strip(),
                                  "start": round(w.start, 3), "end": round(w.end, 3),
                                  "prob": round(w.probability, 3)})
        out_json = out_dir / f"{video.stem}.json"
        out_json.write_text(json.dumps({"source": str(video.resolve()), "engine": "whisper", "model": args.model,
                                        "language": info.language, "words": words},
                                       ensure_ascii=False, indent=1))
        lines = [f"[{a:07.2f}-{b:07.2f}] {t}" for a, b, t in phrases(words)]
        (out_dir / f"{video.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"[{n}/{len(todo)}] {video.name} : {len(words)} mots, {info.duration:.0f}s d'audio "
              f"en {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
