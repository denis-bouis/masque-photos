# Masque Photos

Compose un habillage type Garmin (titre, nom d'événement, statistiques) par-dessus
une ou plusieurs photos d'activité (sous-projet [[2026-Masque-Photos]]). La compétence
**orchestre** le script `~/Dev/masque-photos/masque_photos.py`.

Détail du gabarit de référence : [[Analyse-visuel-référence-Garmin]].

Deux modes :
- **Uniforme** — un seul titre/lieu/stats appliqué à toutes les photos du dossier d'entrée.
- **Par date** (recommandé pour un lot étalé sur plusieurs jours, ex. un trek) — chaque
  date de prise de vue a son propre titre/lieu/stats, mémorisés dans un fichier
  manifest JSON réutilisable d'une session à l'autre.

Tracé GPS optionnel : si Denis fournit un ou plusieurs fichiers GPX, le script trace
un tracé GPS stylisé (ligne + point de départ + flèche d'arrivée) sur une bande
horizontale de la photo (22% de la hauteur, positionnée à 28% par défaut). Le tracé
est mis à l'échelle pour tenir dans sa boîte — **pas** projeté sur le terrain réel de
la photo (aucune donnée de pose caméra disponible). Détection de visage (OpenCV,
optionnelle) : si la bande par défaut croiserait un visage détecté, elle est déplacée
au-dessus ou en dessous selon la place disponible entre le bandeau titre et le bloc
stats ; si aucune place n'est disponible dans ces bornes, la collision est assumée
(rare). Sans opencv installé, dégradation silencieuse vers la position par défaut.

## Flux — mode par date (défaut si les photos couvrent plusieurs dates)

### 1. Demander le dossier d'entrée, le dossier de sortie, le nom de l'événement
Le nom de l'événement (pavé "GARMIN" remplacé) et la couleur d'accent restent
**uniformes** sur tout le lot, même en mode par date.

### 2. Lister les dates
```bash
"$PY" "$SCRIPT" --in-dir "<dossier-entrée>" --list-dates [--manifest "<manifest.json>"]
```
Donne, pour chaque date détectée (triée croissante) : nombre de photos, statut dans
le manifest (`présent`/`absent`), noms de fichiers.

Manifest par défaut : `<dossier-entrée>/manifest.json` (co-localisé avec les photos,
sauf si Denis précise un autre chemin — utile pour rejouer/compléter le lot plus tard).

### 3. Compléter le manifest — uniquement les dates `absent`
Pour chaque date signalée `absent` (dans l'ordre chronologique), demander à Denis :
titre, lieu, et les statistiques à afficher (mêmes règles qu'en mode uniforme :
dénivelé +/- regroupés sur une ligne si les deux sont fournis, ne rien inventer).
Poser ces questions **en une seule fois pour toutes les dates manquantes** plutôt que
d'enchaîner les allers-retours, sauf si Denis préfère répondre au fur et à mesure.

Les dates déjà `présent` dans le manifest ne sont **jamais** redemandées : leurs
valeurs existantes sont conservées telles quelles.

Écrire/mettre à jour le fichier manifest (`Write`/`Edit`) au format :
```json
{
  "2025-11-10": {
    "titre": "J1 - ...",
    "lieu": "...",
    "stat": ["DURÉE|5h30|", "DISTANCE|18|km"],
    "gpx": "in/activity_XXXX.gpx"
  },
  "2025-11-11": { "titre": "...", "lieu": "...", "stat": [] }
}
```
`stat` suit le format `LABEL|VALEUR|UNITÉ` (même syntaxe qu'en CLI), liste vide si
aucune stat pour cette date. `gpx` est optionnel — chemin vers un fichier GPX (absolu,
ou relatif au dossier du manifest) ; si absent, aucun tracé n'est dessiné pour cette
date. Ne jamais deviner ou réutiliser le GPX d'une autre date.

### 4. Exécuter le rendu
```bash
"$PY" "$SCRIPT" --in-dir "<dossier-entrée>" --out-dir "<dossier-sortie>" \
  --event-name "<Nom-Événement>" --manifest "<manifest.json>" \
  --couleur "<optionnel>"
```
Le script regroupe les photos par date, applique le titre/lieu/stats de la date
correspondante, et échoue explicitement si une date du dossier n'est pas dans le
manifest (ne devrait pas arriver après l'étape 3). Génère systématiquement les
versions gauche et droite par photo.

### 5. Montrer le résultat
Afficher au moins une paire gauche/droite par date distincte générée, annoncer le
dossier de sortie et le nombre total d'images produites.

## Flux — mode uniforme (une seule date, ou lot homogène)

Si toutes les photos partagent la même activité/date, ou si Denis demande explicitement
un habillage identique pour tout le lot, sauter le manifest :

```bash
"$PY" "$SCRIPT" \
  --in-dir "<photo-unique-ou-dossier>" \
  --out-dir "<dossier-de-sortie>" \
  --event-name "<Nom-Événement>" \
  --titre "<Titre-Activité>" \
  --date-heure "<Date, optionnel>" \
  --lieu "<Lieu>" \
  --stat "DURÉE|<valeur>|" \
  --stat "DISTANCE|<valeur>|km" \
  --stat "DÉNIVELÉ + / -|<D+> / <D->|m" \
  --stat "ALTITUDE MIN / MAX|<min> / <max>|m" \
  --couleur "<#RRGGBB-ou-nom, optionnel>" \
  --gpx "<fichier.gpx, optionnel>"
```

`--date-heure` omis ⇒ date dérivée automatiquement de l'EXIF de chaque photo (repli
sur le nom de fichier `YYYYMMDD_HHMMSS`), **sans l'heure** dans le sous-titre affiché.

## Variables

- Python : `~/Dev/masque-photos/.venv/bin/python`
- Script : `~/Dev/masque-photos/masque_photos.py` — **lien symbolique** vers le fichier
  maître conservé dans le vault, `10-PROJETS/2026-Masque-Photos/Réalisation/masque_photos.py`.
  Toujours éditer l'un ou l'autre chemin indifféremment (même fichier) ; ne jamais
  recréer une copie séparée, cela romprait le lien et ferait diverger les deux versions.
- Police titres/valeurs : DIN Condensed Bold (système, `/System/Library/Fonts/Supplemental/`)

## Détails communs aux deux modes

`--stat` (mode uniforme) / `stat` (manifest) est répétable, dans l'ordre d'affichage
souhaité ; omettre les lignes non fournies par Denis. Ne jamais inventer une valeur
manquante — l'omettre plutôt qu'un placeholder.

`--in-dir` accepte indifféremment un fichier ou un dossier ; si c'est un dossier,
toutes les images qu'il contient (`.jpg`, `.jpeg`, `.png`, non récursif) sont traitées.

`--couleur` est optionnel — omettre le flag si Denis ne précise rien (défaut : blanc,
fond noir du pavé événement). S'applique au fond du pavé événement, au titre et aux
stats ; le texte du nom d'événement bascule automatiquement noir/blanc pour le contraste.

## Règles de sécurité

- Le dossier de sortie est toujours distinct du dossier d'entrée — jamais d'écrasement
  des photos sources.
- Ne pas inventer de données d'activité manquantes — omettre la ligne plutôt que
  de fabriquer une valeur.
- Ne jamais redemander une date déjà présente dans le manifest ; ne jamais écraser
  silencieusement une entrée existante du manifest sans confirmation de Denis.
- Opération sans risque (pas de suppression, pas de modification de fichiers
  existants hors manifest) — aucune validation préalable requise avant exécution.
