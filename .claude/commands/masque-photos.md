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

Cinq étapes strictement séquentielles : identifier les dates → extraire les GPX
Garmin Connect correspondants → générer le manifest initial → inviter Denis à
corriger/valider → générer sur son **go explicite** (jamais de génération
automatique dès que le manifest est complet).

### 1. Identifier les dates des photos
Demander le **dossier événement** (si non fourni) — un dossier unique contenant un
sous-dossier `in/` avec les photos, un sous-dossier `gpx/` pour les fichiers GPX
(isolé de `in/`, rempli à l'étape 2), un sous-dossier `out/` pour les rendus, et le
manifest à sa racine. Demander aussi le nom de l'événement (pavé "GARMIN" remplacé)
et la couleur d'accent optionnelle — ces deux derniers sont **globaux** (uniformes sur
tout le lot). Si les photos ne sont pas encore dans `<dossier-événement>/in/`, les y
placer avant de continuer.

```bash
"$PY" "$SCRIPT" --dossier "<dossier-événement>" --list-dates
```
Relever la date min et la date max détectées (bornes de la période à couvrir pour
l'extraction GPX).

### 2. Extraire les GPX Garmin Connect correspondants
```bash
"$PY" ~/Dev/masque-photos/export_garmin_gpx.py --debut <date-min> --fin <date-max> --out-dir "<dossier-événement>/gpx"
```
Télécharge dans `<dossier-événement>/gpx/` (isolé de `in/`, directement repris par
`--dossier` à l'étape suivante) tous les fichiers GPX Garmin Connect de la période —
idempotent (fichiers déjà présents ignorés). Authentification via jeton mis en cache
(`~/.garminconnect`), sans prompt si la session est déjà valide.

**Si la commande échoue avec un message d'authentification** (jeton absent/expiré,
aucun terminal interactif disponible pour ressaisir les identifiants) : ne pas tenter
de contourner — demander à Denis de lancer ce script **lui-même** une fois dans un
terminal (`"$PY" ~/Dev/masque-photos/export_garmin_gpx.py --debut ... --fin ... --out-dir ...`),
puis relancer cette étape une fois la session réauthentifiée.

### 3. Générer le manifest initial
```bash
"$PY" "$SCRIPT" --dossier "<dossier-événement>" --init-manifest \
  --event-name "<Nom-Événement>" [--couleur "<optionnel>"]
```
Le script scanne `<dossier-événement>/in/` (photos regroupées par date) et
`<dossier-événement>/gpx/` (fichiers `.gpx`), rapproche chaque GPX de sa date (date du
premier point du tracé) et écrit/complète le manifest à la racine du dossier
événement : `event_name`/`couleur` globaux, une entrée par date détectée (`stat: []`,
`gpx` renseigné si un fichier a été rapproché — chemin relatif du type
`gpx/activity_XXXX.gpx`, `titre` **repris du nom de piste GPX** `<trk><name>` si
disponible et pas déjà renseigné — plusieurs segments le même jour donnent des noms
joints par ` / `). `lieu` reste toujours vide (aucune donnée GPX ne le fournit).

**Idempotent** : une date ou un champ déjà renseigné (y compris `titre` auto-rempli à
une exécution précédente, `event_name`/`couleur` si non refournis) n'est jamais
écrasé. Un GPX dont la date ne correspond à aucune photo est ignoré — le signaler à
Denis s'il s'attendait à un rapprochement.

Manifest : `<dossier-événement>/manifest.json` (racine du dossier événement, pas dans
`in/`). La sortie du script liste, pour chaque date : nombre de photos, statut
(`rempli`/`à compléter` — une date reste `à compléter` tant que `lieu` est vide, même
avec un `titre` auto-rempli), et le GPX rapproché le cas échéant.

### 4. Inviter Denis à corriger/valider le manifest
Présenter à Denis, **en une seule fois**, toutes les dates avec leur `titre` proposé
(repris du GPX — à valider ou corriger, ce n'est qu'une proposition) et demander :
confirmation ou correction du titre, le lieu (toujours à fournir), et les statistiques
à afficher (dénivelé +/- regroupés sur une ligne si les deux sont fournis, ne rien
inventer). Ne pas se contenter d'accepter silencieusement les titres auto-remplis —
les montrer explicitement pour que Denis puisse les ajuster.

Si un GPX rapproché automatiquement à l'étape 3 semble incorrect (mauvaise date), le
signaler à Denis plutôt que de le corriger silencieusement.

Écrire les réponses dans le manifest (`Edit`), format d'une entrée de date :
```json
"2025-11-10": {
  "titre": "J1 - ...",
  "lieu": "...",
  "stat": ["DURÉE|5h30|", "DISTANCE|18|km"],
  "gpx": "gpx/activity_XXXX.gpx"
}
```
`stat` suit le format `LABEL|VALEUR|UNITÉ` (même syntaxe qu'en CLI), liste vide si
aucune stat pour cette date. `gpx` peut aussi être une **liste** de chemins si
plusieurs activités distinctes existent le même jour (rapprochées automatiquement à
l'étape 3, ne jamais fusionner manuellement deux fichiers en un seul).

### 5. Demander confirmation, puis générer
Une fois toutes les dates `rempli`, résumer à Denis ce qui va être généré (nombre de
dates, nombre de photos, event/couleur) et **attendre son go explicite** avant de lancer
le rendu — ne jamais enchaîner automatiquement après l'étape 4.

```bash
"$PY" "$SCRIPT" --dossier "<dossier-événement>"
```
`--event-name`/`--couleur` ne sont plus nécessaires ici (lus depuis le manifest) ; ne
les refournir que si Denis veut explicitement les changer par rapport à l'étape 3. Le
script échoue explicitement si une date du dossier est absente du manifest, ou si son
titre/lieu est vide (garde-fou si l'étape 4 n'est pas terminée). Génère systématiquement
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

## Mode --duo (mode par date uniquement)

Recadrage automatique (`clamp_aspect_for_instagram`) au ratio maximum accepté par Instagram
(portrait 9:16, paysage 1.91:1) — voir section suivante. Pour un contrôle total du cadrage
(ex. respecter le 4:5 strict du feed sans aucune perte), Denis peut fournir directement 2
photos déjà cadrées par date au lieu d'une seule :

```bash
"$PY" "$SCRIPT" --dossier "<dossier-événement>" --duo
```

Attend exactement **2 photos par date** dans `in/`, déjà au ratio souhaité, nommées
`<YYYYMMDD_HHMMSS>-scene.jpg` et `<YYYYMMDD_HHMMSS>-stats.jpg` :
- **scene** : nom d'événement, date, lieu et tracé GPS (pas de titre, pas de stats)
- **stats** : titre et statistiques (pas de nom d'événement, pas de date/lieu, pas de tracé)

Une seule image générée par photo (pas de variante gauche/droite — les 2 photos sont déjà
cadrées par Denis, la logique gauche/droite d'évitement de visage n'a plus lieu d'être).
Échoue explicitement si une date n'a pas exactement ces 2 photos avec ces suffixes.

## Recadrage automatique au ratio Instagram

Toutes les photos sont recadrées (centré) à `clamp_aspect_for_instagram` avant incrustation
du texte : portrait max 9:16, paysage max 1.91:1 — au-delà, Instagram recadrerait lui-même à
la publication, de façon imprévisible, risquant de couper le bandeau titre ou le bloc stats
près des bords. Le recadrage a lieu **avant** l'incrustation, donc titre/stats se
repositionnent automatiquement dans les nouvelles dimensions. Sans effet sur une photo déjà
dans ces bornes (mode --duo avec photos pré-cadrées en 4:5, par exemple).

⚠️ Recadrer signifie perdre une partie de la photo (ex. ~17% de hauteur pour une photo très
verticale ramenée à 9:16). Les vignettes de la grille de profil Instagram restent, elles,
recadrées en carré (1:1) par Instagram lui-même — sans solution native pour l'éviter sur un
post photo/carrousel (contrairement aux reels, qui acceptent une image de couverture séparée).

## Script compagnon — export des GPX depuis Garmin Connect

`~/Dev/masque-photos/export_garmin_gpx.py` (étape 2 du flux par date) télécharge en
masse les GPX Garmin Connect entre deux dates. Orchestrable par la compétence car
l'authentification ne bloque jamais silencieusement : session réutilisée depuis le
jeton en cache (`~/.garminconnect`) si valide, sinon échec **immédiat et explicite**
(pas de prompt en attente) demandant à Denis de lancer le script lui-même une fois
dans un terminal pour (ré)authentifier. Ne jamais tenter de saisir des identifiants
Garmin à la place de Denis.

## Variables

- Python : `~/Dev/masque-photos/.venv/bin/python`
- Script : `~/Dev/masque-photos/masque_photos.py` — **lien symbolique** vers le fichier
  maître conservé dans le vault, `10-PROJETS/2026-Masque-Photos/Réalisation/masque_photos.py`.
  Toujours éditer l'un ou l'autre chemin indifféremment (même fichier) ; ne jamais
  recréer une copie séparée, cela romprait le lien et ferait diverger les deux versions.
- Script d'export GPX : `~/Dev/masque-photos/export_garmin_gpx.py` — même principe de
  lien symbolique vers le fichier maître du vault.
- Police titres/valeurs : DIN Condensed Bold (système, `/System/Library/Fonts/Supplemental/`)

## Détails communs aux deux modes

`--stat` (mode uniforme) / `stat` (manifest) est répétable, dans l'ordre d'affichage
souhaité ; omettre les lignes non fournies par Denis. Ne jamais inventer une valeur
manquante — l'omettre plutôt qu'un placeholder.

`--dossier` (mode par date, recommandé) dérive `in/`, `gpx/`, `out/` et
`manifest.json` — ne fournir `--in-dir`/`--gpx-dir`/`--out-dir`/`--manifest`
explicitement que pour s'écarter de cette convention (cas rare).

`--in-dir` accepte indifféremment un fichier ou un dossier ; si c'est un dossier,
toutes les images qu'il contient (`.jpg`, `.jpeg`, `.png`, non récursif) sont traitées.

`--couleur` est optionnel — omettre le flag si Denis ne précise rien (défaut : blanc,
fond noir du pavé événement). S'applique au fond du pavé événement, au titre et aux
stats ; le texte du nom d'événement bascule automatiquement noir/blanc pour le contraste.

## Règles de sécurité

- Le dossier de sortie est toujours distinct du dossier d'entrée — jamais d'écrasement
  des photos sources.
- Ne pas inventer de données d'activité manquantes — omettre la ligne plutôt que
  de fabriquer une valeur. Un `titre` auto-rempli depuis un nom de piste GPX n'est
  **pas** une invention (donnée réelle du fichier) mais reste une proposition à
  montrer explicitement à Denis pour validation/correction (étape 4), jamais à traiter
  comme définitif sans qu'il l'ait vue.
- Ne jamais redemander une date déjà `rempli` dans le manifest ; ne jamais écraser
  silencieusement une entrée existante (titre/lieu/stat/gpx/event_name/couleur) sans
  confirmation de Denis — `--init-manifest` est idempotent par conception, mais toute
  correction manuelle d'un champ déjà rempli doit être explicitement validée par Denis.
- **Jamais de génération sans le go explicite de Denis** après l'étape 4 (compléter le
  manifest) — même si toutes les dates sont `rempli`.
- **Jamais de saisie d'identifiants Garmin à la place de Denis.** L'étape 2
  (`export_garmin_gpx.py`) échoue immédiatement et explicitement si la session en
  cache est absente/expirée plutôt que d'attendre un prompt — dans ce cas, demander à
  Denis de relancer le script lui-même dans un terminal.
- Opération sans risque (pas de suppression, pas de modification de fichiers
  existants hors manifest) — aucune validation préalable requise avant les étapes 1 à
  3 (`--list-dates`, extraction GPX idempotente, `--init-manifest`) ; seule la
  génération finale (étape 5) attend le go de Denis.
