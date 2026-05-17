"""Push-Notification Backend-Routes & Scheduler-Integration."""
import json
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, abort, current_app
from flask_login import login_required, current_user
from extensions import db
from models import User, Match, Prediction

push_bp = Blueprint("push", __name__, url_prefix="/push")


def _vapid_config():
    cfg = current_app.config
    pub = cfg.get("VAPID_PUBLIC_KEY", "")
    priv = cfg.get("VAPID_PRIVATE_KEY", "")
    claim = cfg.get("VAPID_CLAIM_EMAIL", "mailto:admin@tippspiel.local")
    return pub, priv, claim


@push_bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Keine Daten"}), 400
    current_user.push_subscription = json.dumps(data)
    db.session.commit()
    return jsonify({"ok": True}), 201


@push_bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    current_user.push_subscription = None
    db.session.commit()
    return jsonify({"ok": True})


@push_bp.route("/vapid-public-key")
def vapid_public_key():
    pub, _, _ = _vapid_config()
    if not pub:
        return jsonify({"error": "VAPID nicht konfiguriert"}), 503
    return jsonify({"publicKey": pub})


@push_bp.route("/test", methods=["POST"])
@login_required
def push_test():
    if not current_user.is_admin:
        abort(403)
    payload = {
        "title": "⚽ Test-Push",
        "body": "Push-Benachrichtigungen funktionieren! 🎉",
        "url": "/dashboard",
        "tag": "test-push",
    }
    sent, failed = _send_push_to_users([current_user], payload)
    return jsonify({"sent": sent, "failed": failed})


@push_bp.route("/send-reminder", methods=["POST"])
@login_required
def push_send_reminder():
    if not current_user.is_admin:
        abort(403)
    data = request.get_json(silent=True) or {}
    match_id = data.get("match_id")
    if match_id:
        from models import Match
        match = Match.query.get_or_404(match_id)
        sent, failed = _remind_for_match(match)
    else:
        sent, failed = _remind_upcoming()
    return jsonify({"sent": sent, "failed": failed})


def _send_push_to_users(users, payload: dict) -> tuple[int, int]:
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        current_app.logger.warning("pywebpush nicht installiert – Push deaktiviert.")
        return 0, 0
    pub, priv, claim = _vapid_config()
    if not pub or not priv:
        current_app.logger.warning("VAPID Keys fehlen – Push deaktiviert.")
        return 0, 0
    sent = 0
    failed = 0
    for user in users:
        if not user.push_subscription:
            continue
        try:
            sub = json.loads(user.push_subscription)
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims={
                    "sub": claim,
                    "exp": int((datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()),
                },
            )
            sent += 1
        except Exception as e:
            current_app.logger.warning(f"Push fehlgeschlagen für User {user.id}: {e}")
            if "410" in str(e) or "404" in str(e):
                user.push_subscription = None
                db.session.commit()
            failed += 1
    return sent, failed


def _remind_for_match(match) -> tuple[int, int]:
    all_users = User.query.filter(
        User.push_subscription.isnot(None),
        ~User.email.like("%@bot.local"),
    ).all()
    reminded_users = []
    for user in all_users:
        has_tip = Prediction.query.filter_by(user_id=user.id, match_id=match.id).first()
        if not has_tip:
            reminded_users.append(user)
    if not reminded_users:
        return 0, 0
    kickoff_str = match.kickoff.strftime("%H:%M Uhr")
    payload = {
        "title": "⚽ Tipp-Erinnerung!",
        "body": (
            f"{match.home_team.short_name} – {match.away_team.short_name} "
            f"startet um {kickoff_str}. Du hast noch nicht getippt!"
        ),
        "url": f"/schedule/{match.matchday}",
        "tag": f"reminder-{match.id}",
        "icon": "/static/uploads/logo_192.png",
    }
    return _send_push_to_users(reminded_users, payload)


def _remind_upcoming() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    upcoming = Match.query.filter(
        Match.kickoff > now,
        Match.kickoff <= now + timedelta(hours=1, minutes=5),
        Match.status == "scheduled",
    ).all()
    total_sent = 0
    total_failed = 0
    for match in upcoming:
        s, f = _remind_for_match(match)
        total_sent += s
        total_failed += f
    return total_sent, total_failed


def push_reminder_job(app):
    """Wird vom Scheduler aufgerufen."""
    with app.app_context():
        sent, failed = _remind_upcoming()
        if sent > 0:
            print(f"[{datetime.now(timezone.utc)}] Push-Reminder: {sent} gesendet, {failed} fehlgeschlagen")


def register_push_routes(app):
    app.register_blueprint(push_bp)
