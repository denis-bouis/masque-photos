#!/usr/bin/env python3
"""
Export Garmin Connect — télécharge en masse les fichiers GPX des activités
existant entre deux dates, en vue de leur utilisation par masque_photos.py
(--init-manifest / --gpx-dir).

Prérequis : pip install garminconnect

Authentification : jamais d'identifiants en argument CLI (visibles dans
l'historique shell). Email/mot de passe lus depuis GARMIN_EMAIL/GARMIN_PASSWORD
si définis, sinon demandés interactivement (mot de passe masqué). Un code MFA
est demandé si le compte l'exige. La session est mise en cache dans
~/.garminconnect — les exécutions suivantes ne redemandent rien tant que le
jeton est valide, y compris quand le script est orchestré par la compétence
/masque-photos (aucun prompt bloquant sans terminal interactif : échec
immédiat et explicite si le jeton est absent/expiré et qu'aucun terminal
n'est disponible pour ressaisir les identifiants — Denis doit alors lancer
le script une fois lui-même dans un terminal).

Usage :
    python export_garmin_gpx.py --debut 2025-11-10 --fin 2025-11-25 --out-dir ./in
"""

import argparse
import getpass
import os
import sys
from datetime import date, datetime
from pathlib import Path

try:
    from garminconnect import Garmin, GarminConnectAuthenticationError
except ImportError:
    print("✗ Dépendance manquante : pip install garminconnect", file=sys.stderr)
    sys.exit(1)

TOKEN_STORE = Path.home() / ".garminconnect"


def _require_interactive(reason: str):
    """Refuse de bloquer sur un input() sans terminal interactif (ex. orchestré par
    la compétence Claude Code, qui ne peut pas répondre à un prompt en cours de
    commande) — échoue immédiatement avec un message clair plutôt que de rester
    en attente indéfiniment."""
    if not sys.stdin.isatty():
        raise GarminConnectAuthenticationError(
            f"{reason} — aucun terminal interactif disponible. Lancer ce script "
            "manuellement dans un terminal pour (ré)authentifier une fois ; la "
            "session est ensuite mise en cache (~/.garminconnect) et réutilisée "
            "automatiquement, y compris quand le script est orchestré."
        )


def _prompt_mfa() -> str:
    _require_interactive("Code MFA Garmin Connect requis")
    return input("Code MFA Garmin Connect : ").strip()


def _try_login(email: str | None, password: str | None) -> Garmin:
    client = Garmin(email, password, prompt_mfa=_prompt_mfa)
    client.login(str(TOKEN_STORE))
    return client


def login() -> Garmin:
    """Tente d'abord une session silencieuse via le jeton mis en cache ; ne
    demande les identifiants que si ce jeton est absent ou expiré."""
    try:
        return _try_login(None, None)
    except GarminConnectAuthenticationError:
        pass

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        _require_interactive("Identifiants Garmin Connect requis (GARMIN_EMAIL/GARMIN_PASSWORD absents)")
        email = email or input("Email Garmin Connect : ").strip()
        password = password or getpass.getpass("Mot de passe Garmin Connect : ")
    return _try_login(email, password)


def parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Date invalide (attendu AAAA-MM-JJ) : {raw}") from e


def main():
    parser = argparse.ArgumentParser(
        description="Télécharge les GPX des activités Garmin Connect entre deux dates (incluses).")
    parser.add_argument("--debut", required=True, type=parse_date, help="Date de début, AAAA-MM-JJ")
    parser.add_argument("--fin", required=True, type=parse_date, help="Date de fin, AAAA-MM-JJ")
    parser.add_argument("--out-dir", required=True, type=Path, help="Dossier de sortie des fichiers .gpx")
    args = parser.parse_args()

    if args.fin < args.debut:
        print("✗ --fin doit être postérieure ou égale à --debut", file=sys.stderr)
        sys.exit(1)

    try:
        client = login()
    except GarminConnectAuthenticationError as e:
        print(f"✗ Authentification refusée : {e}", file=sys.stderr)
        sys.exit(1)

    activities = client.get_activities_by_date(args.debut.isoformat(), args.fin.isoformat(), sortorder="asc")
    if not activities:
        print(f"✗ Aucune activité trouvée entre {args.debut} et {args.fin}", file=sys.stderr)
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    for act in activities:
        activity_id = act["activityId"]
        name = act.get("activityName", "")
        start = act.get("startTimeLocal", "")
        out_path = args.out_dir / f"activity_{activity_id}.gpx"

        if out_path.exists():
            print(f"– Déjà présent : {out_path.name}  ({start} — {name})")
            continue

        try:
            gpx_bytes = client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.GPX)
        except Exception as e:
            print(f"✗ Échec du téléchargement pour l'activité {activity_id} ({name}) : {e}", file=sys.stderr)
            continue

        out_path.write_bytes(gpx_bytes)
        print(f"✓ {out_path.name}  ({start} — {name})")


if __name__ == "__main__":
    main()
