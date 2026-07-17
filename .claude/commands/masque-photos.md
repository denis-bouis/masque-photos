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

Trois étapes strictement séquentielles : initialiser → compléter → générer sur le
**go explicite** de Denis (jamais de génération automatique dès que le manifest est complet).

### 1. Initialiser le manifest
Demander le dossier d'entrée, le dossier de sortie, le nom de l'événement (pavé
"GARMIN" remplacé) et la couleur d'accent optionnelle — ces deux derniers sont
**globaux** (uniformes sur tout le lot). Demander aussi où se trouvent les fichiers
GPX s'ils ne sont pas dans le dossier d'entrée (`--gpx-dir`).

```bash
"$PY" "$SCRIPT" --in-dir "<dossier-entrée>" --init-manifest \
  --event-name "<Nom-Événement>" [--couleur "<optionnel>"] [--gpx-dir "<dossier-gpx>"] \
  [--manifest "<manifest.json>"]
```

Le script scanne les photos (regroupées par date) et les fichiers `.gpx` du dossier
indiqué, rapproche chaque GPX de sa date (date du premier point du tracé) et écrit/
complète le manifest : `event_name`/`couleur` globaux, une entrée par date détectée
(`titre`/`lieu` vides, `stat: []`, `gpx` renseigné si un fichier a été rapproché).
**Idempotent** : une date ou un champ déjà renseigné (y compris `event_name`/`couleur`
si `--event-name`/`--couleur` ne sont pas refournis) n'est jamais écrasé. Un GPX dont
la date ne correspond à aucune photo est ignoré — le signaler à Denis s'il s'attendait
à un rapprochement.

Manifest par défaut : `<dossier-entrée>/manifest.json` (co-localisé avec les photos,
sauf si Denis précise un autre chemin — utile pour rejouer/compléter le lot plus tard).

La sortie du script liste, pour chaque date : nombre de photos, statut
(`rempli`/`à compléter`), et le GPX rapproché le cas échéant.

### 2. Compléter le manifest — uniquement les dates `à compléter`
Pour chaque date signalée `à compléter` (dans l'ordre chronologique), demander à
Denis : titre, lieu, et les statistiques à afficher (dénivelé +/- regroupés sur une
ligne si les deux sont fournis, ne rien inventer). Poser ces questions **en une seule
fois pour toutes les dates manquantes** plutôt que d'enchaîner les allers-retours,
sauf si Denis préfère répondre au fur et à mesure.

Les dates déjà `rempli` ne sont **jamais** redemandées : leurs valeurs existantes sont
conservées telles quelles. Si un GPX rapproché automatiquement à l'étape 1 semble
incorrect (mauvaise date), le signaler à Denis plutôt que de le corriger silencieusement.

Écrire les réponses dans le manifest (`Edit`), format d'une entrée de date :
```json
"2025-11-10": {
  "titre": "J1 - ...",
  "lieu": "...",
  "stat": ["DURÉE|5h30|", "DISTANCE|18|km"],
  "gpx": "in/activity_XXXX.gpx"
}
```
`stat` suit le format `LABEL|VALEUR|UNITÉ` (même syntaxe qu'en CLI), liste vide si
aucune stat pour cette date.

### 3. Demander confirmation, puis générer
Une fois toutes les dates `rempli`, résumer à Denis ce qui va être généré (nombre de
dates, nombre de photos, event/couleur) et **attendre son go explicite** avant de lancer
le rendu — ne jamais enchaîner automatiquement après l'étape 2.

```bash
"$PY" "$SCRIPT" --in-dir "<dossier-entrée>" --out-dir "<dossier-sortie>" \
  --manifest "<manifest.json>"
```
`--event-name`/`--couleur` ne sont plus nécessaires ici (lus depuis le manifest) ; ne
les refournir que si Denis veut explicitement les changer par rapport à l'étape 1. Le
script échoue explicitement si une date du dossier est absente du manifest, ou si son
titre/lieu est vide (garde-fou si l'étape 2 n'est pas terminée). Génère systématiquement
les versions gauche et droite par photo.

Montrer au moins une paire gauche/droite par date distincte générée, annoncer le
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

## Script compagnon — export des GPX depuis Garmin Connect

`~/Dev/masque-photos/export_garmin_gpx.py` télécharge en masse les fichiers GPX des
activités Garmin Connect existant entre deux dates, en vue de leur utilisation par
`--gpx-dir` à l'étape 1. Denis le lance lui-même dans un terminal (identifiants et
code MFA saisis interactivement, jamais via la compétence) :
```bash
"$PY" ~/Dev/masque-photos/export_garmin_gpx.py --debut AAAA-MM-JJ --fin AAAA-MM-JJ --out-dir "<dossier-gpx>"
```
Ne pas orchestrer ce script depuis la compétence (authentification Garmin
interactive à un service tiers) — seulement le mentionner à Denis s'il a besoin de
récupérer des GPX avant l'étape 1.

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
- Ne jamais redemander une date déjà `rempli` dans le manifest ; ne jamais écraser
  silencieusement une entrée existante (titre/lieu/stat/gpx/event_name/couleur) sans
  confirmation de Denis — `--init-manifest` est idempotent par conception, mais toute
  correction manuelle d'un champ déjà rempli doit être explicitement validée par Denis.
- **Jamais de génération sans le go explicite de Denis** après l'étape 2 (compléter le
  manifest) — même si toutes les dates sont `rempli`.
- Opération sans risque (pas de suppression, pas de modification de fichiers
  existants hors manifest) — aucune validation préalable requise avant `--init-manifest`
  ou `--list-dates` ; seule la génération finale attend le go de Denis.
