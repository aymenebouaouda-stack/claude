"""Envoie un gros fichier dans le Google Drive de l'utilisateur (upload « resumable », par blocs).

Nécessite un jeton d'accès OAuth temporaire fourni par l'utilisateur (ex. via OAuth 2.0
Playground, portée https://www.googleapis.com/auth/drive.file, valable ~1 h), lu dans un
fichier (jamais en argument de commande) : ~/.config/gdrive/token (une ligne).

Usage:
    python3 upload_drive.py <fichier> [--name "Nom dans Drive"] [--folder <id_dossier>]
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 32 * 1024 * 1024  # multiple de 256 Kio, exigé par l'API


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--name")
    ap.add_argument("--folder")
    ap.add_argument("--mime", default="video/mp4")
    args = ap.parse_args()

    token = (Path.home() / ".config" / "gdrive" / "token").read_text().strip()
    size = args.file.stat().st_size
    meta = {"name": args.name or args.file.name}
    if args.folder:
        meta["parents"] = [args.folder]
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,size,webViewLink",
        data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": args.mime, "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            session = r.headers["Location"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Drive a refusé l'envoi (HTTP {e.code}) : {e.read()[:400].decode(errors='replace')}")

    sent = 0
    with args.file.open("rb") as f:
        while sent < size:
            block = f.read(CHUNK)
            end = sent + len(block) - 1
            put = urllib.request.Request(session, data=block, method="PUT", headers={
                "Content-Length": str(len(block)), "Content-Range": f"bytes {sent}-{end}/{size}"})
            try:
                with urllib.request.urlopen(put, timeout=600) as r:
                    info = json.loads(r.read() or b"{}")
                    print(f"terminé : {info.get('name')} — {info.get('webViewLink')}")
                    return
            except urllib.error.HTTPError as e:
                if e.code != 308:  # 308 = bloc reçu, continuer
                    raise SystemExit(f"échec à {sent} octets (HTTP {e.code}) : {e.read()[:300].decode(errors='replace')}")
            sent = end + 1
            print(f"  {sent / size:.0%} envoyé", flush=True)


if __name__ == "__main__":
    main()
