# Analyse : TikTok @babenda.bf « les trois outils » (fourni par l'utilisateur le 2026-10-09)

Fichier reçu : MP4 576×1024, 30 i/s, 57,8 s, avec audio. Lien d'origine :
https://vm.tiktok.com/ZGdCr3KmS/ (inaccessible depuis l'environnement cloud).

Méthode : l'audio n'a pas pu être transcrit (modèles bloqués par le réseau). Le texte ci-dessous
vient des **sous-titres incrustés**, lus à 2,5 images/s avec `caption_strip.py`
(`--top 0.735 --height 0.11`). Des mots affichés moins de 0,4 s ont pu être manqués ;
les passages entre crochets sont incertains.

## Texte (reconstitué depuis les sous-titres)

> En fait, j'ai l'impression que personne n'est au courant de cette dinguerie. Tu peux faire
> apprendre n'importe quelle compétence réelle à Claude juste en lui donnant des vidéos YouTube.
> Et par exemple, si tu veux que Claude Code te construise tout ce que [je vais] dire là dans
> cette vidéo direct, t'as juste à sauvegarder cette vidéo et à la partager à ton Claude Code.
> Voilà les trois outils. Le premier, pour donner à Claude Code la capacité de regarder YouTube,
> tu utilises simplement un repo GitHub qui s'appelle claude-video. C'est une liste de skills qui
> lui permettent d'aspirer toute l'info d'une vidéo. Le deuxième, pour aller plus loin, tu
> récupères une clé API Gemini sur Google AI Studio, c'est complètement gratuit parce que Gemini,
> lui, il comprend et lit YouTube nativement. Mais attends, parce que le troisième fait toute la
> [différence]. Tu prends ces deux outils et tu les branches sur un framework d'agents qui
> transforment toutes ces infos en agents ultra compétents. Les gens qui font ça correctement
> prennent une avance de malade. C'est exactement comme ça que j'ai construit mon agent monteur
> vidéo qui a d'ailleurs monté toute cette vidéo. Je t'ai fait un guide complet inspiré de celui
> de James Hetman, commente ce que tu veux en vidéo et je te l'envoie seulement si tu me [follow].

## Les trois outils : vérification

| Outil (vidéo) | Ce qui a été vérifié | Statut ici |
|---|---|---|
| 1. `claude-video` | Dépôt réel : github.com/bradautomates/claude-video, licence MIT (Copyright 2026 Bradley Bonanno), dernier commit 2026-09-25 lu par `git clone`. Skill `/watch` : téléchargement yt-dlp, images (ffmpeg), transcription (sous-titres natifs, WhisperX local, Groq/OpenAI), ou moteur Gemini. Les chiffres affichés à l'écran (15k étoiles, 1,4k forks) **n'ont pas pu être vérifiés**. | Non installé dans le dépôt (code tiers refusé par le contrôle de sécurité). Ses techniques (images horodatées, échantillonnage par scènes, images-repères) sont reprises par `contact_sheet.py`, `scenes.py`, `caption_strip.py`. À installer soi-même sur son ordinateur si souhaité (README du dépôt). |
| 2. Clé API Gemini | `generativelanguage.googleapis.com` est joignable depuis l'environnement cloud. Selon le README de claude-video, les fichiers locaux sont **envoyés chez Google** puis supprimés après la réponse. « Gratuit » et « jusqu'à 1 h » : **non confirmé** — selon un résumé de recherche (page non relue), la doc Google Cloud (Vertex) cite ~45 min avec audio / ~1 h sans audio, mais c'est une autre offre ; les limites du palier gratuit de l'API Gemini n'ont pas pu être lues. | Optionnel. Nécessite une clé de l'utilisateur ET son accord pour envoyer ses vidéos à Google. |
| 3. « Framework d'agents » | Dans la vidéo : écran `/agents` (veille, analyse, rédaction) et `agent-monteur` (transcription mot à mot, découpe des plans, illustrations générées, sous-titres synchronisés, rendu 1080×1920). Dans Claude Code, cela correspond aux sous-agents (`.claude/agents/`). « James Hetman » : **aucune source trouvée**, je ne peux pas confirmer ce guide. | Fait : `.claude/agents/monteur-vlog.md` et `.claude/agents/critique-montage.md`. |

## Leçons de montage (observées directement dans la vidéo)

- **Accroche à 0 s** : phrase choc (« personne n'est au courant de cette dinguerie ») + visuel
  spectaculaire en haut de l'écran pendant que la personne parle.
- **Mise en page « bandeau »** 9:16 : fond noir, face caméra dans une bande horizontale
  (~20 % → ~60 % de la hauteur), visuels et titres dans la bande du haut, sous-titres dessous.
  → `"fit": "band"` dans `render.py`.
- **Sous-titres** : 2–3 mots, casse normale, police serif blanche, sans boîte, sous le
  bandeau ; synchronisés au mot. → style `"serif"`.
- **Titres-mots-clés** : grandes capitales serif condensées sur 1–2 lignes (« UNE COMPÉTENCE
  RÉELLE », « LES TROIS OUTILS », « LE DEUXIÈME », « GRATUIT », « LE TROISIÈME »), qui chevauchent
  le haut de la tête. Ils marquent la structure (1-2-3). → `"titles"`.
  (Effet « texte derrière la personne » : demande un détourage, impossible ici sans modèle.)
- **Rythme** : `scenes.py` mesure 13 coupes franches en 57,8 s (plan moyen 4,1 s, de 1,2 à 7,6 s),
  mais la planche contact montre un changement visuel (titre, carte, capture) environ toutes les
  1,5 s : on change ce qu'on voit sans forcément couper la voix.
- **Preuves à l'écran** : captures (GitHub, Google AI Studio, menu de partage), maquettes de
  terminal animées, carte-liste numérotée (01 LIT YOUTUBE / 02 IMAGE ET SON / 03 JUSQU'À 1 H /
  04 0 €).
- **Illustrations générées** (images cinématiques sombres) pour les idées abstraites (« framework
  d'agents »).
- **Fin** : appel à l'action incrusté (faux champ commentaire « GUIDE ») puis carte de fin.
