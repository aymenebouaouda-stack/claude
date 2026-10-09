"""Transcription horodatée via l'API Gemini, en n'envoyant QUE l'audio (pas l'image).

À n'utiliser qu'avec l'accord explicite de l'utilisateur : l'audio part chez Google
(Files API), puis le fichier est supprimé après la réponse.

Clé lue dans $GEMINI_API_KEY ou dans ~/.config/gemini/.env (GEMINI_API_KEY=...).
Ne jamais mettre la clé dans le dépôt ni dans une ligne de commande.

Sortie (même format que transcribe_local.py) :
    <edit_dir>/transcripts/<nom>.json  {"source", "engine", "model", "language", "words": [...]}
    <edit_dir>/transcripts/<nom>.txt   phrases horodatées lisibles
Les horodatages au mot sont ESTIMÉS (le modèle donne des segments ; les mots sont répartis
dans chaque segment au prorata du nombre de caractères). Vérifier les coupes à l'image.

Usage:
    python3 transcribe_gemini.py <video> --edit-dir <edit_dir> [--model gemini-3.5-flash]
           [--language fr] [--context "noms propres, lieux…"]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com"


def api_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"].strip()
    env = Path.home() / ".config" / "gemini" / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("clé Gemini introuvable (GEMINI_API_KEY ou ~/.config/gemini/.env)")


class Busy(Exception):
    """Modèle surchargé (503/429/500) après plusieurs essais."""


def call(method: str, url: str, key: str, body: bytes | None = None, headers: dict | None = None,
         tries: int = 5):
    h = {"x-goog-api-key": key, **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.headers, r.read()
        except urllib.error.HTTPError as e:
            msg = e.read()[:800].decode(errors="replace")
            if e.code in (429, 500, 503):
                if attempt < tries - 1:
                    time.sleep(15 * 2 ** attempt)
                    continue
                raise Busy(f"Gemini HTTP {e.code} : {msg[:200]}")
            raise SystemExit(f"Gemini HTTP {e.code} : {msg}")
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
            if attempt < tries - 1:
                time.sleep(15)
                continue
            raise Busy(f"réseau : {e}")
    raise Busy("échec après plusieurs essais")


def generate(model: str, body: dict, key: str, tries: int = 3) -> tuple[str, dict]:
    """generateContent en streaming (SSE) : des octets arrivent en continu, ce qui évite les
    coupures de connexion inactive sur les longues requêtes. Renvoie (texte, usageMetadata)."""
    url = f"{API}/v1beta/models/{model}:streamGenerateContent?alt=sse"
    data = json.dumps(body).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            text, usage = [], {}
            with urllib.request.urlopen(req, timeout=900) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[5:])
                    for c in chunk.get("candidates", []):
                        for part in c.get("content", {}).get("parts", []):
                            if "text" in part and not part.get("thought"):
                                text.append(part["text"])
                    usage = chunk.get("usageMetadata", usage)
            return "".join(text), usage
        except urllib.error.HTTPError as e:
            msg = e.read()[:800].decode(errors="replace")
            if e.code in (429, 500, 503):
                if "quota" in msg.lower() or attempt == tries - 1:
                    raise Busy(f"Gemini HTTP {e.code} : {msg[:300]}")
                time.sleep(20 * 2 ** attempt)
                continue
            if e.code in (400, 404):  # modèle inexistant/retiré ou option non prise en charge → modèle suivant
                raise Busy(f"Gemini HTTP {e.code} : {msg[:300]}")
            raise SystemExit(f"Gemini HTTP {e.code} : {msg}")
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
            if attempt == tries - 1:
                raise Busy(f"réseau : {e!r}")
            time.sleep(20)
    raise Busy("échec après plusieurs essais")


def upload(path: Path, key: str, mime: str) -> dict:
    size = path.stat().st_size
    hdrs, _ = call("POST", f"{API}/upload/v1beta/files", key,
                   json.dumps({"file": {"display_name": path.name}}).encode(),
                   {"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(size),
                    "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"})
    up_url = hdrs["X-Goog-Upload-URL"]
    _, body = call("POST", up_url, key, path.read_bytes(),
                   {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
                    "Content-Length": str(size)})
    f = json.loads(body)["file"]
    while f.get("state") == "PROCESSING":
        time.sleep(3)
        _, body = call("GET", f"{API}/v1beta/{f['name']}", key)
        f = json.loads(body)
    if f.get("state") != "ACTIVE":
        raise SystemExit(f"fichier refusé par Gemini : {f.get('state')}")
    return f


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING"},
        "segments": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
            "speaker": {"type": "STRING"}, "text": {"type": "STRING"}},
            "required": ["start", "end", "text"]}},
    },
    "required": ["segments"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--fallback", nargs="*", default=["gemini-3.8-flash", "gemini-3.6-flash", "gemini-flash-latest"],
                    help="Modèles essayés si le principal est surchargé")
    ap.add_argument("--language", default="fr")
    ap.add_argument("--context", default="", help="Noms propres et lieux pour l'orthographe")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_dir = args.edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{args.video.stem}.json"
    if out_json.exists() and not args.force:
        print(f"cache : {out_json}")
        return
    key = api_key()

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{args.video.stem}.m4a"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(args.video), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "aac", "-b:a", "48k", "-y", str(audio)], check=True)
        f = upload(audio, key, "audio/mp4")
        try:
            prompt = (
                f"Transcris mot pour mot TOUT ce qui est dit dans cet audio (langue principale : {args.language}). "
                "Garde les hésitations (euh, hum), les faux départs, les rires entre parenthèses, l'argot. "
                "Découpe en segments courts (une phrase ou moins, 6 secondes maximum), avec début et fin en "
                "SECONDES depuis le début du fichier (décimales). N'invente rien : si c'est inaudible, écris "
                "[inaudible]. Ignore la musique sauf si quelqu'un chante. "
                "speaker = « A », « B »… par voix distincte. "
                + (f"Orthographe des noms propres : {args.context}." if args.context else "")
            )
            body = {
                "contents": [{"parts": [{"file_data": {"mime_type": f["mimeType"], "file_uri": f["uri"]}},
                                        {"text": prompt}]}],
                "generationConfig": {"temperature": 0, "response_mime_type": "application/json",
                                     "response_schema": SCHEMA},
            }
            raw, used = None, None
            for model in [args.model, *args.fallback]:
                try:
                    raw, usage = generate(model, body, key)
                    used = model
                    break
                except Busy as e:
                    print(f"{model} indisponible ({e}) → modèle suivant")
            if raw is None:
                raise SystemExit("tous les modèles Gemini sont surchargés ; réessayer plus tard")
        finally:
            try:
                call("DELETE", f"{API}/v1beta/{f['name']}", key)
            except Busy:
                print("suppression du fichier distant impossible (expire seul sous 48 h)")

    data = json.loads(raw)
    words = []
    for seg in data["segments"]:
        toks = seg["text"].split()
        if not toks:
            continue
        a, b = float(seg["start"]), float(seg["end"])
        total = sum(len(t) for t in toks)
        t = a
        for tok in toks:
            d = (b - a) * len(tok) / total
            words.append({"type": "word", "text": tok, "start": round(t, 3), "end": round(t + d, 3),
                          "speaker_id": seg.get("speaker"), "estimated": True})
            t += d
    out_json.write_text(json.dumps({"source": str(args.video.resolve()), "engine": "gemini",
                                    "model": used, "language": data.get("language", args.language),
                                    "segments": data["segments"], "words": words},
                                   ensure_ascii=False, indent=1))
    lines = [f"[{s['start']:07.2f}-{s['end']:07.2f}] {s.get('speaker') or ''} {s['text']}".replace("  ", " ")
             for s in data["segments"]]
    (out_dir / f"{args.video.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(data['segments'])} segments, {len(words)} mots → {out_json} "
          f"(tokens : {usage.get('totalTokenCount', '?')})")


if __name__ == "__main__":
    main()
