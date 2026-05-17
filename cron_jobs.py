"""Cron-Jobs fuer Shared Hosting (Netcup) - Ersatz fuer APScheduler.

Auf Shared-Hosting darfst du keine dauerhaft laufenden Prozesse starten.
Stattdessen ruft Plesk-Cron dieses Script periodisch auf:

  Plesk-Panel -> Geplante Aufgaben (Cron) -> Aufgabe hinzufuegen:
  ----------------------------------------------------------------
  Befehl:   /var/www/vhosts/<domain>/.python-venvs/<app>/bin/python
            /var/www/vhosts/<domain>/<app-pfad>/cron_jobs.py <task>
  Pfad:     <app-pfad>
  Wann:     */15 * * * *   (alle 15 Minuten)
  ----------------------------------------------------------------

Verfuegbare Tasks:
  python cron_jobs.py sync       -> Ergebnisse von API holen
  python cron_jobs.py reminder   -> 1h-Reminder per E-Mail senden
  python cron_jobs.py all        -> Beides nacheinander
"""
import sys
import os
from datetime import datetime, timedelta

# Pfad zum App-Verzeichnis hinzufuegen (falls von cron aufgerufen)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env laden
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def run_sync():
    """Synchronisiert Ergebnisse von football-data.org / OpenLigaDB."""
    from app import app
    from utils import sync_results
    with app.app_context():
        result = sync_results()
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] SYNC: {result['msg']}")
        return result["ok"]


def run_reminders():
    """Sendet Tipp-Erinnerungen fuer Spiele, die in <=1h beginnen."""
    from app import app
    from extensions import db
    from models import Match, User, Prediction
    from utils import send_kickoff_reminder

    with app.app_context():
        now = datetime.utcnow()
        # Spiele in den naechsten 60-65 Minuten (kleines Fenster, damit Cron alle 15min ok ist)
        upcoming = Match.query.filter(
            Match.kickoff > now + timedelta(minutes=55),
            Match.kickoff <= now + timedelta(minutes=70),
            Match.status == "scheduled",
        ).all()

        sent = 0
        for match in upcoming:
            for user in User.query.all():
                pred = Prediction.query.filter_by(
                    user_id=user.id, match_id=match.id
                ).first()
                if not pred:
                    if send_kickoff_reminder(user, match):
                        sent += 1

        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] REMINDER: {sent} Mails fuer {len(upcoming)} Spiele gesendet.")
        return True


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "all"

    if task == "sync":
        run_sync()
    elif task == "reminder":
        run_reminders()
    elif task == "all":
        run_sync()
        run_reminders()
    else:
        print(f"Unbekannte Task: {task}")
        print("Verwendung: python cron_jobs.py [sync|reminder|all]")
        sys.exit(1)


if __name__ == "__main__":
    main()
