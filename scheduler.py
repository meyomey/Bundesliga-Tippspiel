"""APScheduler: Automatische E-Mail-Reminder + API-Sync.

Starte zusammen mit: `python scheduler.py` (parallel zur App)
oder via Worker-Service in Production.
"""
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.blocking import BlockingScheduler

from app import app
from extensions import db
from models import Match, User, Prediction
from utils import send_kickoff_reminder, sync_results


def reminder_job():
    """Erinnert User 1h vor Anpfiff, wenn sie noch nicht getippt haben."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        upcoming = Match.query.filter(
            Match.kickoff > now,
            Match.kickoff <= now + timedelta(hours=1, minutes=5),
            Match.status == "scheduled",
        ).all()

        for match in upcoming:
            for user in User.query.all():
                pred = Prediction.query.filter_by(
                    user_id=user.id, match_id=match.id
                ).first()
                if not pred:
                    send_kickoff_reminder(user, match)
                    print(f"Reminder gesendet an {user.email} für {match.id}")


def sync_job():
    """Synct Ergebnisse alle 15 Minuten."""
    with app.app_context():
        res = sync_results()
        print(f"[{datetime.now(timezone.utc)}] Sync: {res['msg']}")


if __name__ == "__main__":
    sched = BlockingScheduler()
    sched.add_job(reminder_job, "interval", minutes=10, id="reminders")
    sched.add_job(sync_job, "interval", minutes=15, id="sync")
    print("⏰ Scheduler gestartet (Reminder: alle 10min, Sync: alle 15min)")
    sched.start()
