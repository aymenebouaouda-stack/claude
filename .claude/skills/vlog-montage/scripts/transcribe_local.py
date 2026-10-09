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
import re
import subprocess
import tempfile
import time
from pathlib import Path


# Hallucinations typiques de Whisper sur musique/silence (génériques de sous-titreurs, etc.)
HALLU = re.compile(r"sous-titr|amara\.org|radio-canada|merci d'avoir regard|abonnez-vous|"
                   r"sous titres|st' ?501|j[ée]r[ée]my diaz", re.IGNORECASE)


def clean(words: list[dict]) -> list[dict]:
    """Retire les phrases (groupes séparés par ≥ 0.5 s) qui ressemblent à une hallucination."""
    out, cur = [], []
    for w in words + [None]:
        if cur and (w is None or w["start"] - cur[-1]["end"] >= 0.5):
            if not HALLU.search(" ".join(x["text"] for x in cur)):
                out += cur
            cur = []
        if w is not None:
            cur.append(w)
    return out


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
    ap.add_argument("--clean-only", action="store_true",
                    help="Nettoie les transcriptions existantes (hallucinations) sans retranscrire")
    args = ap.parse_args()

    out_dir = args.edit_dir / "transcripts"
    if args.clean_only:
        for v in args.videos:
            f = out_dir / f"{v.stem}.json"
            if f.exists():
                d = json.loads(f.read_text())
                before = len(d["words"])
                d["words"] = clean(d["words"])
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                (out_dir / f"{v.stem}.txt").write_text(
                    "\n".join(f"[{a:07.2f}-{b:07.2f}] {t}" for a, b, t in phrases(d["words"])), encoding="utf-8")
                if before != len(d["words"]):
                    print(f"{v.name} : {before - len(d['words'])} mot(s) halluciné(s) retiré(s)")
        return
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

    import numpy as np
    from faster_whisper import WhisperModel  # import tardif : seul ce script en dépend

    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    prompt = "Euh, alors, hum, en fait, du coup… Voilà." + (f" {args.prompt}" if args.prompt else "")
    for n, video in enumerate(todo, 1):
        t0 = time.time()
        if True:
            # Décodage par ffmpeg (évite la dépendance à PyAV, parfois incompatible)
            pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                                  "-f", "s16le", "-"], capture_output=True, check=True).stdout
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = model.transcribe(
                audio, language=args.language, word_timestamps=True,
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
                    # Un jeton sans espace initial prolonge le mot précédent (c + 'est, Vas + -y)
                    if words and w.word and not w.word.startswith(" ") and w.word[0] in "'’-":
                        words[-1]["text"] += w.word.strip()
                        words[-1]["end"] = round(w.end, 3)
                        words[-1]["prob"] = round(min(words[-1]["prob"], w.probability), 3)
                        continue
                    words.append({"type": "word", "text": w.word.strip(),
                                  "start": round(w.start, 3), "end": round(w.end, 3),
                                  "prob": round(w.probability, 3)})
        words = clean(words)
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
