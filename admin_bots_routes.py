"""Admin-Routes für KI-Bot-Verwaltung."""
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models import User, Match, Prediction

BOT_NAMES = ["RookieBot", "AmateurBot", "ProBot", "ExpertBot", "MasterBot"]
BOT_LEVELS = {"RookieBot": 1, "AmateurBot": 2, "ProBot": 3, "ExpertBot": 4, "MasterBot": 5}


def _get_bots():
    return User.query.filter(User.email.like("%@bot.local")).all()


def _current_matchday():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    next_match = Match.query.filter(Match.status == "scheduled").order_by(Match.kickoff).first()
    if next_match:
        return next_match.matchday
    last = Match.query.filter(Match.status == "finished").order_by(Match.matchday.desc()).first()
    return last.matchday if last else 1


def _get_bot_active_status(bot_name):
    from utils import get_setting
    return get_setting(f"bot_active_{bot_name}", "1") == "1"


def _admin_bots_view():
    bots = _get_bots()
    matchday = _current_matchday()
    bot_list = []
    for b in bots:
        tip_count = Prediction.query.filter_by(user_id=b.id).count()
        exact_count = Prediction.query.filter_by(user_id=b.id, points=4).count()
        active = _get_bot_active_status(b.username)
        bot_list.append({
            "user": b,
            "name": b.username,
            "level": BOT_LEVELS.get(b.username, 1),
            "tips": tip_count,
            "exact": exact_count,
            "points": b.total_points() if hasattr(b, "total_points") else 0,
            "active": active,
        })
    bot_list.sort(key=lambda x: x["points"], reverse=True)
    total_tips = sum(b["tips"] for b in bot_list)
    open_matches = Match.query.filter_by(status="scheduled").count()
    active_count = sum(1 for b in bot_list if b["active"])
    return render_template(
        "admin/bots.html", bots=bot_list, current_matchday=matchday,
        total_tips=total_tips, open_matches=open_matches, active_count=active_count,
    )


def _admin_bots_tip_all():
    from ai_opponent import ai_manager
    matchday = int(request.form.get("matchday", _current_matchday()))
    overwrite = request.form.get("overwrite") == "1"
    try:
        results = ai_manager.tip_all_matches(matchday=matchday, overwrite=overwrite)
        tipped = sum(r.get("tipped", 0) for r in results.values())
        skipped = sum(r.get("skipped", 0) for r in results.values())
        if tipped > 0:
            flash(f"✅ {tipped} Bot-Tipps für Spieltag {matchday} abgegeben", "success")
        else:
            flash(f"ℹ️ Keine neuen Tipps für Spieltag {matchday}.", "info")
    except Exception as e:
        flash(f"❌ Fehler beim Tippen: {e}", "error")
    return redirect(url_for("admin.admin_bots"))


def _admin_bots_tip_single():
    from ai_opponent import ai_manager
    bot_id = int(request.form.get("bot_id"))
    matchday = int(request.form.get("matchday", _current_matchday()))
    bot_user = User.query.get(bot_id)
    if not bot_user or "@bot.local" not in bot_user.email:
        flash("❌ Bot nicht gefunden.", "error")
        return redirect(url_for("admin.admin_bots"))
    bot_name = bot_user.username
    try:
        opponent = ai_manager.get_opponent(bot_name)
        if opponent is None:
            flash(f"❌ KI-Opponent '{bot_name}' nicht gefunden.", "error")
            return redirect(url_for("admin.admin_bots"))
        matches = Match.query.filter_by(matchday=matchday, status="scheduled").all()
        tipped = 0
        for match in matches:
            existing = Prediction.query.filter_by(user_id=bot_id, match_id=match.id).first()
            if not existing:
                home_tip, away_tip = opponent.get_tip(match)
                db.session.add(Prediction(
                    user_id=bot_id, match_id=match.id,
                    home_tip=home_tip, away_tip=away_tip,
                    joker=False, points=0,
                ))
                tipped += 1
        db.session.commit()
        flash(f"✅ {bot_name}: {tipped} Tipps für Spieltag {matchday} abgegeben.", "success")
    except Exception as e:
        flash(f"❌ Fehler: {e}", "error")
    return redirect(url_for("admin.admin_bots"))


def _admin_bots_reset():
    bot_id = int(request.form.get("bot_id"))
    matchday = int(request.form.get("matchday", _current_matchday()))
    bot_user = User.query.get(bot_id)
    if not bot_user or "@bot.local" not in bot_user.email:
        flash("❌ Bot nicht gefunden.", "error")
        return redirect(url_for("admin.admin_bots"))
    match_ids = [m.id for m in Match.query.filter_by(matchday=matchday).all()]
    deleted = Prediction.query.filter(
        Prediction.user_id == bot_id,
        Prediction.match_id.in_(match_ids),
    ).delete(synchronize_session=False)
    db.session.commit()
    flash(f"🗑️ {deleted} Tipps von {bot_user.username} für Spieltag {matchday} gelöscht.", "warning")
    return redirect(url_for("admin.admin_bots"))


def _admin_bots_toggle():
    from utils import get_setting, set_setting
    bot_name = request.form.get("bot_name", "").strip()
    if bot_name not in BOT_NAMES:
        flash("❌ Unbekannter Bot.", "error")
        return redirect(url_for("admin.admin_bots"))
    key = f"bot_active_{bot_name}"
    current = get_setting(key, "1")
    new_val = "0" if current == "1" else "1"
    set_setting(key, new_val)
    status = "aktiviert" if new_val == "1" else "deaktiviert"
    flash(f"🤖 {bot_name} {status}.", "success")
    return redirect(url_for("admin.admin_bots"))
