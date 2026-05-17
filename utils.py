"""Hilfsfunktionen: Punkteberechnung, API-Sync, Mail, Badges, Avatar."""
import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from PIL import Image
from flask import current_app, url_for
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from extensions import db, mail
from models import (
    User, Team, Match, Prediction, Setting, Badge, UserBadge,
    SpecialQuestion, SpecialPrediction, SeasonArchive, MatchdayWinner,
)


# ---------------------------------------------------------------- Settings -
def get_setting(key, default=None):
    s = Setting.query.get(key)
    if s and s.value is not None:
        try:
            return json.loads(s.value)
        except (json.JSONDecodeError, ValueError):
            return s.value
    return default


def set_setting(key, value):
    s = Setting.query.get(key)
    serialized = json.dumps(value)
    if s:
        s.value = serialized
    else:
        db.session.add(Setting(key=key, value=serialized))
    db.session.commit()


# ----------------------------------------------------------- Punkte-Logik -
def calculate_points(prediction, match):
    """Berechnet die Punkte für einen Tipp basierend auf den Einstellungen."""
    if match.home_score is None or match.away_score is None:
        return 0
    if match.status != "finished":
        return 0

    points_exact = get_setting("points_exact", current_app.config["POINTS_EXACT"])
    points_diff = get_setting("points_diff", current_app.config["POINTS_DIFF"])
    points_tendency = get_setting("points_tendency", current_app.config["POINTS_TENDENCY"])

    h, a = prediction.home_tip, prediction.away_tip
    rh, ra = match.home_score, match.away_score
    base = 0

    if h == rh and a == ra:
        base = points_exact
    elif (h - a) == (rh - ra) and (rh != ra or h != a):
        # gleiche Differenz, aber nicht beide Unentschieden mit anderem Score
        base = points_diff
    elif (h > a and rh > ra) or (h < a and rh < ra) or (h == a and rh == ra):
        base = points_tendency

    if prediction.joker:
        base *= 2
    return base



def calculate_points_for_score(prediction, home_score, away_score):
    """Berechnet Punkte fuer einen Tipp gegen ein beliebiges Ergebnis (ohne DB-Zugriff).
    Wird fuer Live-Scoring verwendet – aendert nichts an prediction.points."""
    if home_score is None or away_score is None:
        return 0

    points_exact = get_setting("points_exact", current_app.config["POINTS_EXACT"])
    points_diff = get_setting("points_diff", current_app.config["POINTS_DIFF"])
    points_tendency = get_setting("points_tendency", current_app.config["POINTS_TENDENCY"])

    h, a = prediction.home_tip, prediction.away_tip
    rh, ra = home_score, away_score
    base = 0

    if h == rh and a == ra:
        base = points_exact
    elif (h - a) == (rh - ra) and (rh != ra or h != a):
        base = points_diff
    elif (h > a and rh > ra) or (h < a and rh < ra) or (h == a and rh == ra):
        base = points_tendency

    if prediction.joker:
        base *= 2
    return base


def classify_prediction_live(prediction, match):
    """Klassifiziert einen Tipp fuer die Live-Anzeige.
    Fuer finished Matches: wie bisher.
    Fuer live Matches: berechnet gegen aktuellen Score.
    Fuer scheduled: pending."""
    if match.status == "scheduled" or match.home_score is None or match.away_score is None:
        return "pending"
    if match.status == "finished":
        return classify_prediction(prediction, match)
    # Live: berechne gegen aktuellen Score
    h, a = prediction.home_tip, prediction.away_tip
    rh, ra = match.home_score, match.away_score
    if h == rh and a == ra:
        return "exact"
    if (h - a) == (rh - ra) and (rh != ra or h != a):
        return "diff"
    if (h > a and rh > ra) or (h < a and rh < ra) or (h == a and rh == ra):
        return "tendency"
    return "wrong"


def get_live_user_stats(user, matchday=None):
    """Wie get_user_stats, aber beruecksichtigt LIVE-Scores fuer laufende Spiele.
    NICHT cachen – muss immer frisch sein!"""
    from flask import session
    from models import Competition
    active_code = session.get("competition_code") if session else None
    comp = None
    if active_code:
        comp = Competition.query.filter_by(code=active_code, is_active=True).first()

    q = Prediction.query.filter_by(user_id=user.id)
    if comp:
        q = q.join(Match).filter(Match.competition_id == comp.id)
        if matchday:
            q = q.filter(Match.matchday == matchday)
    else:
        if matchday:
            q = q.join(Match).filter(Match.matchday == matchday)
    preds = q.all()

    counters = {"exact": 0, "diff": 0, "tendency": 0, "wrong": 0, "pending": 0}
    total_pts = 0
    joker_used = 0

    for p in preds:
        kind = classify_prediction_live(p, p.match)
        counters[kind] += 1
        if p.match.status == "finished":
            total_pts += (p.points or 0)
        elif p.match.status == "live":
            total_pts += calculate_points_for_score(p, p.match.home_score, p.match.away_score)
        if p.joker:
            joker_used += 1

    # Sonderpunkte
    sp_pts = 0
    if not matchday:
        sp = SpecialPrediction.query.filter_by(user_id=user.id).all()
        sp_pts = sum(s.points or 0 for s in sp)

    finished = counters["exact"] + counters["diff"] + counters["tendency"] + counters["wrong"]
    quote = round((counters["exact"] / finished) * 100) if finished else 0

    return {
        "user": user,
        "points": total_pts + sp_pts,
        "match_points": total_pts,
        "special_points": sp_pts,
        "tips": len(preds),
        "exact": counters["exact"],
        "diff": counters["diff"],
        "tendency": counters["tendency"],
        "wrong": counters["wrong"],
        "pending": counters["pending"],
        "joker_used": joker_used,
        "exact_quote": quote,
    }


def get_live_leaderboard(matchday=None):
    """Live-Rangliste: Punkte werden fuer laufende Spiele DYNAMISCH berechnet.
    Kein Caching – muss immer aktuell sein!"""
    all_users = User.query.all()
    active_users = []
    for u in all_users:
        if u.username.endswith('Bot'):
            is_active = get_setting(f"bot_active_{u.username}", True)
            if not is_active:
                continue
        active_users.append(u)

    rows = [get_live_user_stats(u, matchday=matchday) for u in active_users]
    rows.sort(key=lambda r: (
        -r["points"], -r["exact"], -r["diff"], -r["tendency"], -r["tips"], r["user"].username
    ))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def recalculate_all_points():
    """Geht alle finished Matches durch und schreibt die Punkte neu.
    Anschließend werden Spieltagsieger neu berechnet."""
    matches = Match.query.filter_by(status="finished").all()
    for m in matches:
        for p in m.predictions:
            p.points = calculate_points(p, m)
    db.session.commit()
    # Spieltagsieger automatisch neu berechnen
    try:
        recompute_matchday_winners()
    except Exception as e:
        current_app.logger.warning(f"recompute_matchday_winners failed: {e}")


# --------------------------------------------------------- Avatar Upload -
def save_avatar(file_storage, user_id):
    """Speichert hochgeladenes Avatar als 300x300 PNG.
    Liefert (filename, error_message) zurueck.
    error_message ist None bei Erfolg oder wenn keine Datei mitgesendet wurde.
    """
    # Robust: Feld kann None, leerer String, FileStorage ohne Filename,
    # oder echtes FileStorage sein
    if not file_storage:
        return None, None
    # WTForms liefert bei leerem FileField manchmal "" zurueck
    if isinstance(file_storage, str):
        return None, None
    if not getattr(file_storage, "filename", None):
        return None, None

    # Dateiendung pruefen
    allowed = current_app.config.get("ALLOWED_EXTENSIONS", {"png", "jpg", "jpeg", "gif", "webp"})
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in allowed:
        return None, f"Dateityp .{ext} nicht erlaubt. Erlaubt: {', '.join(sorted(allowed))}"

    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)

    filename = secure_filename(f"avatar_{user_id}_{datetime.now(timezone.utc).timestamp():.0f}.png")
    filepath = os.path.join(upload_dir, filename)

    try:
        img = Image.open(file_storage)
        img.thumbnail((300, 300))
        # Transparenz-erhaltend speichern: RGBA → RGB nur, wenn nicht durchsichtig
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (20, 20, 20))
            try:
                background.paste(img, mask=img.split()[3])
                img = background
            except (IndexError, ValueError):
                img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        img.save(filepath, "PNG", optimize=True)
        return filename, None
    except Exception as e:
        current_app.logger.error(f"Avatar-Upload fehlgeschlagen: {e}")
        return None, f"Bild konnte nicht verarbeitet werden ({type(e).__name__}). Bitte JPG/PNG verwenden."


# -------------------------------------------------------- Mail / Tokens -
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_reset_token(user_id, expires_sec=3600):
    return _serializer().dumps({"uid": user_id})


def verify_reset_token(token, max_age=3600):
    try:
        data = _serializer().loads(token, max_age=max_age)
        return User.query.get(data["uid"])
    except Exception:
        return None


def apply_mail_settings():
    """Übernimmt gespeicherte SMTP-Einstellungen aus der DB in app.config und die Mail-Instanz.
    Flask-Mail liest diese Werte beim Senden."""
    current_app.config["MAIL_SERVER"] = get_setting("mail_server", current_app.config.get("MAIL_SERVER", "")) or ""
    current_app.config["MAIL_PORT"] = int(get_setting("mail_port", current_app.config.get("MAIL_PORT", 587)) or 587)
    current_app.config["MAIL_USERNAME"] = get_setting("mail_username", current_app.config.get("MAIL_USERNAME", "")) or ""
    current_app.config["MAIL_PASSWORD"] = get_setting("mail_password", current_app.config.get("MAIL_PASSWORD", "")) or ""
    current_app.config["MAIL_DEFAULT_SENDER"] = get_setting("mail_default_sender", current_app.config.get("MAIL_DEFAULT_SENDER", "")) or ""
    current_app.config["MAIL_USE_TLS"] = bool(get_setting("mail_use_tls", current_app.config.get("MAIL_USE_TLS", True)))
    current_app.config["MAIL_USE_SSL"] = bool(get_setting("mail_use_ssl", current_app.config.get("MAIL_USE_SSL", False)))
    
    # Direkt in das mail-Singleton-Objekt injizieren um dynamisches Ueberschreiben zu garantieren
    mail.server = current_app.config["MAIL_SERVER"]
    mail.port = current_app.config["MAIL_PORT"]
    mail.username = current_app.config["MAIL_USERNAME"]
    mail.password = current_app.config["MAIL_PASSWORD"]
    mail.use_tls = current_app.config["MAIL_USE_TLS"]
    mail.use_ssl = current_app.config["MAIL_USE_SSL"]
    mail.default_sender = current_app.config["MAIL_DEFAULT_SENDER"]

    # Zusaetzlich den internen Flask-Mail Status-Cache in app.extensions aktualisieren!
    if "mail" in current_app.extensions:
        state = current_app.extensions["mail"]
        state.server = current_app.config["MAIL_SERVER"]
        state.port = current_app.config["MAIL_PORT"]
        state.username = current_app.config["MAIL_USERNAME"]
        state.password = current_app.config["MAIL_PASSWORD"]
        state.use_tls = current_app.config["MAIL_USE_TLS"]
        state.use_ssl = current_app.config["MAIL_USE_SSL"]
        state.default_sender = current_app.config["MAIL_DEFAULT_SENDER"]


def send_email(subject, recipients, body, html=None):
    apply_mail_settings()
    
    # Console Logging Fallback fuer lokales Testen
    mail_server = current_app.config.get("MAIL_SERVER")
    mail_user = current_app.config.get("MAIL_USERNAME")
    
    print("\n" + "="*60)
    print(f"📧 [E-Mail-Versand angestoßen]")
    print(f"Betreff: {subject}")
    print(f"An: {', '.join(recipients)}")
    print(f"Inhalt:\n{body}")
    print("="*60 + "\n")

    if not mail_server or not mail_user:
        current_app.logger.warning(f"⚠️ E-Mail nicht konfiguriert (MAIL_SERVER/MAIL_USERNAME fehlen). Versand simuliert.")
        return True  # Behandle Simulierungen im lokalen Test als Erfolg damit der User fortfahren kann

    try:
        # Google/Gmail blockiert den Mailversand, wenn die From-Adresse (Envelope-Sender) 
        # nicht mit dem authentifizierten Google-Account uebereinstimmt!
        if mail_server and "gmail.com" in mail_server.lower():
            # Ueberschreibe From-Adresse mit dem angemeldeten Gmail-User
            sender = mail_user
            # Setze das originale Default-Sender-Feld als Reply-To damit Antworten ankommen
            reply_to = current_app.config.get("MAIL_DEFAULT_SENDER") or mail_user
        else:
            sender = current_app.config.get("MAIL_DEFAULT_SENDER") or mail_user
            reply_to = None

        msg = Message(
            subject=subject, 
            recipients=recipients, 
            body=body, 
            html=html, 
            sender=sender,
            reply_to=reply_to
        )
        mail.send(msg)
        current_app.logger.info(f"✅ E-Mail erfolgreich via SMTP ({mail_server}) gesendet an: {recipients}")
        return True
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        current_app.logger.error(f"❌ SMTP E-Mail-Fehler beim Senden via {mail_server}:{current_app.config.get('MAIL_PORT')}:\n{err_msg}")
        print(f"❌ SMTP Fehler: {e}. Link oben kann trotzdem im Terminal verwendet werden!")
        return True  # Lokale Ausweichlösung: Behandle als Erfolg für flüssige Testbarkeit


def send_password_reset(user):
    token = generate_reset_token(user.id)
    reset_url = url_for("auth.password_reset", token=token, _external=True)
    body = f"""Hallo {user.username},

um dein Passwort zurückzusetzen, klicke bitte auf folgenden Link (gültig 1h):
{reset_url}

Falls du diese Anfrage nicht gestellt hast, ignoriere diese E-Mail.
"""
    send_email("Passwort zurücksetzen – Wulmstörper Tipprunde", [user.email], body)


def send_kickoff_reminder(user, match):
    body = f"""Hallo {user.username},

der Anpfiff von {match.home_team.name} vs. {match.away_team.name} steht in 1 Stunde an!
Du hast noch keinen Tipp abgegeben. Schnell zur App: {url_for('main.schedule', _external=True)}
"""
    send_email("Tipp-Erinnerung – Anpfiff in 1h", [user.email], body)


# -------------------------------------------------------- API Sync -
BUNDESLIGA_TEAMS = [
    # Bundesliga Saison 2025/26
    ("FC Bayern München",       "FCB", 5,   "https://crests.football-data.org/5.png",   "#DC052D"),
    ("Borussia Dortmund",       "BVB", 4,   "https://crests.football-data.org/4.png",   "#FDE100"),
    ("Bayer 04 Leverkusen",     "B04", 3,   "https://crests.football-data.org/3.png",   "#E32219"),
    ("RB Leipzig",              "RBL", 721, "https://crests.football-data.org/721.png", "#DD0741"),
    ("VfB Stuttgart",           "VFB", 10,  "https://crests.football-data.org/10.png",  "#E32219"),
    ("Eintracht Frankfurt",     "SGE", 19,  "https://crests.football-data.org/19.png",  "#E1000F"),
    ("VfL Wolfsburg",           "WOB", 11,  "https://crests.football-data.org/11.png",  "#65B32E"),
    ("Borussia Mönchengladbach","BMG", 18,  "https://crests.football-data.org/18.png",  "#000000"),
    ("SC Freiburg",             "SCF", 17,  "https://crests.football-data.org/17.png",  "#5B5B5B"),
    ("1. FC Union Berlin",      "FCU", 28,  "https://crests.football-data.org/28.png",  "#EB1923"),
    ("TSG Hoffenheim",          "TSG", 2,   "https://crests.football-data.org/2.png",   "#1961B5"),
    ("1. FSV Mainz 05",         "M05", 15,  "https://crests.football-data.org/15.png",  "#C8102E"),
    ("FC Augsburg",             "FCA", 16,  "https://crests.football-data.org/16.png",  "#BA3733"),
    ("SV Werder Bremen",        "SVW", 12,  "https://crests.football-data.org/12.png",  "#1D9053"),
    ("1. FC Heidenheim",        "FCH", 44,  "https://crests.football-data.org/44.png",  "#E2001A"),
    ("FC St. Pauli",            "STP", 24,  "https://crests.football-data.org/24.png",  "#62351D"),
    # Aufsteiger Saison 2025/26
    ("Hamburger SV",            "HSV", 269, "https://crests.football-data.org/269.png", "#0F4D92"),
    ("1. FC Köln",              "KOE", 1,   "https://crests.football-data.org/1.png",   "#ED1C24"),
]


def compute_pot_summary():
    """Berechnet die aktuelle Pott-Übersicht.
    Liefert: {amount_per, currency, paid_count, total_count, pot_total, intro}"""
    amount = int(get_setting("pot_amount", 5))
    currency = get_setting("pot_currency", "€")
    intro = get_setting("pot_intro", "")
    paid = User.query.filter_by(has_paid=True).count()
    total = User.query.count()
    return {
        "amount_per": amount,
        "currency": currency,
        "intro": intro,
        "paid_count": paid,
        "total_count": total,
        "pot_total": amount * paid,
        "pot_target": amount * total,
        "missing_count": total - paid,
    }


def recompute_matchday_winners():
    """Berechnet die Spieltagsieger neu basierend auf aktuellen Tipp-Punkten.
    Wird nach jedem Sync und manueller Ergebnis-Eintragung aufgerufen.

    Pro abgeschlossenem Spieltag:
      - finden wir den User mit den meisten Punkten an diesem Spieltag
      - bei Gleichstand: alle bekommen den Sieg (is_shared=True)
      - Tiebreaker: mehr exakte Tipps gewinnt (sonst geteilt)
    """
    from sqlalchemy import func

    season = get_setting("current_season", "2025/26")
    points_exact = get_setting("points_exact", 4)

    # Nur Spieltage mit mind. einem 'finished' Match auswerten
    finished_mds = db.session.query(Match.matchday).filter_by(status="finished").distinct().all()
    finished_md_set = {md for (md,) in finished_mds}

    # Bestehende Winner für diese Saison wegwerfen → komplett neu rechnen
    MatchdayWinner.query.filter_by(season=season).delete()

    for md in sorted(finished_md_set):
        # Punkte aller User für diesen Spieltag
        results = db.session.query(
            Prediction.user_id,
            func.coalesce(func.sum(Prediction.points), 0).label("pts"),
        ).join(Match, Prediction.match_id == Match.id) \
         .filter(Match.matchday == md, Match.status == "finished") \
         .group_by(Prediction.user_id) \
         .all()

        if not results:
            continue
        max_pts = max(r.pts for r in results)
        if max_pts <= 0:
            continue   # keiner hat Punkte → kein Sieger

        # Alle User mit max_pts ermitteln
        top_user_ids = [r.user_id for r in results if r.pts == max_pts]

        # Tiebreaker: mehr exakte Tipps gewinnt
        if len(top_user_ids) > 1:
            exact_counts = {}
            for uid in top_user_ids:
                n_exact = db.session.query(func.count(Prediction.id)) \
                    .join(Match, Prediction.match_id == Match.id) \
                    .filter(
                        Prediction.user_id == uid,
                        Match.matchday == md, Match.status == "finished",
                        Prediction.points >= points_exact,
                    ).scalar() or 0
                exact_counts[uid] = n_exact
            max_exact = max(exact_counts.values())
            top_user_ids = [uid for uid, n in exact_counts.items() if n == max_exact]

        is_shared = len(top_user_ids) > 1

        for uid in top_user_ids:
            # exact_count des Siegers
            n_exact = db.session.query(func.count(Prediction.id)) \
                .join(Match, Prediction.match_id == Match.id) \
                .filter(
                    Prediction.user_id == uid,
                    Match.matchday == md, Match.status == "finished",
                    Prediction.points >= points_exact,
                ).scalar() or 0
            db.session.add(MatchdayWinner(
                matchday=md, user_id=uid, points=max_pts,
                exact_count=n_exact, is_shared=is_shared, season=season,
            ))
    db.session.commit()


# ============================================================
# Trend-Tracking (Tabellenverlauf zwischen Spieltagen)
# ============================================================
def get_user_trend(user_id, last_n_matchdays=8):
    """Liefert eine Sparkline-Datenreihe (Punkte pro Spieltag)
    + Tabellenposition vor/nach letztem Spieltag (für Trend-Pfeil).
    """
    from sqlalchemy import func
    finished_mds = sorted({m for (m,) in db.session.query(Match.matchday)
                           .filter_by(status="finished").distinct().all()})
    if not finished_mds:
        return {"sparkline": [], "current_rank": None,
                "previous_rank": None, "delta": 0, "matchdays": []}

    # Sparkline: Punkte pro Spieltag (auch 0 wenn nicht getippt)
    md_points = {}
    for md in finished_mds[-last_n_matchdays:]:
        pts = db.session.query(func.coalesce(func.sum(Prediction.points), 0)) \
            .join(Match, Prediction.match_id == Match.id) \
            .filter(Prediction.user_id == user_id, Match.matchday == md,
                    Match.status == "finished").scalar() or 0
        md_points[md] = pts
    sparkline = [md_points.get(md, 0) for md in finished_mds[-last_n_matchdays:]]
    matchdays_used = finished_mds[-last_n_matchdays:]

    # Aktueller vs. vorheriger Rang
    current_rank = _compute_rank_through(user_id, finished_mds[-1])
    previous_rank = (
        _compute_rank_through(user_id, finished_mds[-2])
        if len(finished_mds) >= 2 else current_rank
    )
    delta = (previous_rank - current_rank) if (previous_rank and current_rank) else 0

    return {
        "sparkline": sparkline,
        "matchdays": matchdays_used,
        "current_rank": current_rank,
        "previous_rank": previous_rank,
        "delta": delta,
    }


def _compute_rank_through(user_id, max_matchday):
    """Hilfsfunktion: berechnet den Rang eines Users
    nach allen Spieltagen ≤ max_matchday."""
    from sqlalchemy import func
    rows = db.session.query(
        Prediction.user_id,
        func.coalesce(func.sum(Prediction.points), 0).label("pts"),
    ).join(Match, Prediction.match_id == Match.id) \
     .filter(Match.matchday <= max_matchday, Match.status == "finished") \
     .group_by(Prediction.user_id).all()
    # Alle User auch ohne Tipps
    all_users = User.query.all()
    pts_by = {r.user_id: r.pts for r in rows}
    ranking = sorted(
        [(u.id, pts_by.get(u.id, 0)) for u in all_users],
        key=lambda x: -x[1],
    )
    for i, (uid, _) in enumerate(ranking, 1):
        if uid == user_id:
            return i
    return None


# ============================================================
# Tipp-Stil-Insights für /profil
# ============================================================
def get_user_insights(user):
    """Aggregiert lustige & informative Stats über das Tipp-Verhalten."""
    from collections import Counter
    preds = user.predictions.all()
    if not preds:
        return None

    # Häufigste Tipp-Kombination
    combos = Counter(f"{p.home_tip}:{p.away_tip}" for p in preds)
    most_common_tip, mct_count = combos.most_common(1)[0]

    # Heim/Unentsch/Auswärts-Verteilung
    n_home = sum(1 for p in preds if p.home_tip > p.away_tip)
    n_draw = sum(1 for p in preds if p.home_tip == p.away_tip)
    n_away = sum(1 for p in preds if p.home_tip < p.away_tip)
    total = len(preds)

    # Bestes / schlechtestes Spiel
    best = max(preds, key=lambda p: p.points or 0, default=None)
    worst_candidates = [p for p in preds
                        if p.match.status == "finished"
                        and p.points == 0
                        and (p.match.home_score is not None)]
    # "Schlimmster Patzer": größter Tipp-Unterschied bei 0 Punkten
    def diff_to_actual(p):
        return abs(p.home_tip - p.match.home_score) + abs(p.away_tip - p.match.away_score)
    worst = max(worst_candidates, key=diff_to_actual, default=None)

    # Durchschnitt (nur bei finished)
    finished_preds = [p for p in preds if p.match.status == "finished"]
    avg_pts = (sum(p.points or 0 for p in finished_preds) / len(finished_preds)) \
              if finished_preds else 0

    return {
        "most_common_tip": most_common_tip,
        "most_common_count": mct_count,
        "most_common_pct": round(mct_count / total * 100),
        "tendency_pct": {
            "home": round(n_home / total * 100) if total else 0,
            "draw": round(n_draw / total * 100) if total else 0,
            "away": round(n_away / total * 100) if total else 0,
        },
        "best": best,
        "worst": worst,
        "avg_points_per_match": round(avg_pts, 2),
        "total_finished": len(finished_preds),
    }


# ============================================================
# Match-Insights (Vergleich zum Durchschnitt)
# ============================================================
def get_match_tip_distribution(match_id):
    """Aggregiert die Tipp-Verteilung aller User für ein Match."""
    from collections import Counter
    preds = Prediction.query.filter_by(match_id=match_id).all()
    if not preds:
        return None

    n = len(preds)
    n_home = sum(1 for p in preds if p.home_tip > p.away_tip)
    n_draw = sum(1 for p in preds if p.home_tip == p.away_tip)
    n_away = sum(1 for p in preds if p.home_tip < p.away_tip)
    combos = Counter(f"{p.home_tip}:{p.away_tip}" for p in preds)
    most_common, mc_count = combos.most_common(1)[0] if combos else ("—", 0)

    return {
        "n_total": n,
        "tendency_pct": {
            "home": round(n_home / n * 100),
            "draw": round(n_draw / n * 100),
            "away": round(n_away / n * 100),
        },
        "most_common_tip": most_common,
        "most_common_count": mc_count,
        "most_common_pct": round(mc_count / n * 100),
        "all_combos": combos.most_common(5),
    }


# ============================================================
# Wetter-API (Open-Meteo, kostenlos & ohne Key)
# ============================================================
# Stadion-Koordinaten (Top-Genauigkeit, alle 18 Vereine)
STADIUM_COORDS = {
    "FCB": (48.2188, 11.6247),  # Allianz Arena
    "BVB": (51.4925, 7.4519),   # Signal Iduna Park
    "B04": (51.0381, 7.0023),   # BayArena
    "RBL": (51.3458, 12.3481),  # Red Bull Arena
    "VFB": (48.7926, 9.2320),   # MHPArena
    "SGE": (50.0686, 8.6450),   # Deutsche Bank Park
    "WOB": (52.4318, 10.8030),  # Volkswagen Arena
    "BMG": (51.1746, 6.3850),   # Borussia-Park
    "SCF": (47.9892, 7.8298),   # Europa-Park Stadion
    "FCU": (52.4574, 13.5683),  # Stadion An der Alten Försterei
    "TSG": (49.2384, 8.8881),   # PreZero Arena Sinsheim
    "M05": (50.0073, 8.2240),   # MEWA Arena
    "FCA": (48.3231, 10.8861),  # WWK Arena
    "SVW": (53.0664, 8.8378),   # Weserstadion
    "FCH": (48.6678, 10.1414),  # Voith-Arena
    "STP": (53.5546, 9.9657),   # Millerntor-Stadion
    "HSV": (53.5872, 9.8985),   # Volksparkstadion
    "KOE": (50.9335, 6.8754),   # RheinEnergieStadion
}


def get_match_weather(match):
    """Holt Wettervorhersage für das Heimstadion zum Anpfiff.
    Nutzt Open-Meteo (kostenlos, kein Key). Cache: 1h pro Match."""
    if not match or not match.home_team:
        return None
    coords = STADIUM_COORDS.get(match.home_team.short_name)
    if not coords:
        return None

    cache_key = f"weather:{match.id}"
    cached = get_setting(cache_key)
    now_ts = datetime.now(timezone.utc).timestamp()
    if cached and isinstance(cached, dict) and now_ts - cached.get("ts", 0) < 3600:
        return cached.get("data")

    lat, lon = coords
    # Stundengenauigkeit für Anpfiff
    iso_date = match.kickoff.strftime("%Y-%m-%d")
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           f"&hourly=temperature_2m,precipitation,weathercode,windspeed_10m"
           f"&start_date={iso_date}&end_date={iso_date}"
           f"&timezone=Europe%2FBerlin")
    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    # Stündliche Daten — die Stunde des Anpfiffs picken
    times = data.get("hourly", {}).get("time", [])
    target_hour = match.kickoff.strftime("%Y-%m-%dT%H:00")
    idx = None
    for i, t in enumerate(times):
        if t.startswith(target_hour[:13]):
            idx = i
            break
    if idx is None:
        return None

    temp = data["hourly"]["temperature_2m"][idx]
    rain = data["hourly"]["precipitation"][idx]
    wcode = data["hourly"]["weathercode"][idx]
    wind = data["hourly"]["windspeed_10m"][idx]
    weather_info = _weather_code_to_label(wcode)
    result = {
        "temp": round(temp),
        "rain_mm": rain,
        "wind_kmh": round(wind),
        "icon": weather_info["icon"],
        "label": weather_info["label"],
    }
    set_setting(cache_key, {"ts": now_ts, "data": result})
    return result


def _weather_code_to_label(code):
    """WMO Weather-Code → Icon + deutsches Label."""
    table = {
        0: ("☀", "Sonnig"),
        1: ("🌤", "Heiter"),
        2: ("⛅", "Teilweise bewölkt"),
        3: ("☁", "Bewölkt"),
        45: ("🌫", "Nebel"),
        48: ("🌫", "Nebel mit Reif"),
        51: ("🌦", "Leichter Nieselregen"),
        53: ("🌦", "Mäßiger Nieselregen"),
        55: ("🌧", "Starker Nieselregen"),
        61: ("🌦", "Leichter Regen"),
        63: ("🌧", "Mäßiger Regen"),
        65: ("🌧", "Starker Regen"),
        71: ("🌨", "Leichter Schneefall"),
        73: ("🌨", "Mäßiger Schneefall"),
        75: ("❄", "Starker Schneefall"),
        77: ("🌨", "Schneegriesel"),
        80: ("🌦", "Leichte Regenschauer"),
        81: ("🌧", "Mäßige Regenschauer"),
        82: ("⛈", "Heftige Regenschauer"),
        85: ("🌨", "Leichte Schneeschauer"),
        86: ("❄", "Heftige Schneeschauer"),
        95: ("⛈", "Gewitter"),
        96: ("⛈", "Gewitter mit Hagel"),
        99: ("⛈", "Schweres Gewitter mit Hagel"),
    }
    icon, label = table.get(code, ("🌡", "Unbekannt"))
    return {"icon": icon, "label": label}


def generate_season_pdf(user):
    """Erstellt einen schönen PDF-Saison-Report für den User.
    Nutzt reportlab. Liefert BytesIO zurück."""
    from io import BytesIO
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.colors import HexColor, white, black
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return None  # reportlab nicht installiert

    insights = get_user_insights(user)
    stats_total_pts = sum(p.points or 0 for p in user.predictions)
    md_wins = MatchdayWinner.query.filter_by(user_id=user.id).all()
    badges = UserBadge.query.filter_by(user_id=user.id).all()
    total_tips = user.predictions.count()
    finished_preds = [p for p in user.predictions if p.match.status == "finished"]
    n_exact = sum(1 for p in finished_preds
                  if p.points and p.points >= get_setting("points_exact", 4))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=2*cm, rightMargin=2*cm,
                             topMargin=2*cm, bottomMargin=2*cm,
                             title=f"Saison-Report {user.username}")

    # Farben
    teal = HexColor("#14b8a6")
    teal_light = HexColor("#5eead4")
    text_color = HexColor("#0f172a")
    muted = HexColor("#64748b")
    bg_card = HexColor("#f1f5f9")

    # Styles
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                         fontSize=28, textColor=teal,
                         spaceAfter=4, fontName="Helvetica-Bold")
    h2 = ParagraphStyle("h2", parent=styles["Heading2"],
                         fontSize=16, textColor=text_color,
                         spaceBefore=14, spaceAfter=8, fontName="Helvetica-Bold")
    sub = ParagraphStyle("sub", parent=styles["Normal"],
                          fontSize=11, textColor=muted, alignment=TA_LEFT,
                          spaceAfter=12)
    big_num = ParagraphStyle("big", parent=styles["Normal"],
                              fontSize=22, textColor=teal,
                              fontName="Helvetica-Bold", alignment=TA_CENTER)
    label = ParagraphStyle("lbl", parent=styles["Normal"],
                            fontSize=9, textColor=muted, alignment=TA_CENTER,
                            spaceAfter=2)
    body = ParagraphStyle("body", parent=styles["Normal"],
                            fontSize=10, textColor=text_color, alignment=TA_LEFT,
                            spaceAfter=6)

    story = []

    # Header
    story.append(Paragraph("⚽ Wulmstörper Tipprunde", h1))
    story.append(Paragraph(f"Saison-Report: <b>{user.username}</b>", sub))
    if user.full_name:
        story.append(Paragraph(f"<i>{user.full_name}</i>", sub))
    story.append(Spacer(1, 6))

    # Stats-Grid (4 Kacheln)
    cell_style_num = TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), bg_card),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOX", (0,0), (-1,-1), 0.5, HexColor("#e2e8f0")),
        ("ROUNDEDCORNERS", [10,10,10,10]),
        ("TOPPADDING", (0,0), (-1,-1), 14),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
    ])

    def stat_cell(num, lbl):
        return Table([
            [Paragraph(f"<b>{num}</b>", big_num)],
            [Paragraph(lbl, label)],
        ], style=cell_style_num)

    final_rank = "—"
    try:
        from utils import _compute_rank_through
        finished_mds = sorted({m for (m,) in db.session.query(Match.matchday)
                                .filter_by(status="finished").distinct().all()})
        if finished_mds:
            r = _compute_rank_through(user.id, finished_mds[-1])
            if r:
                final_rank = f"#{r}"
    except Exception:
        pass

    stats_grid = Table([[
        stat_cell(stats_total_pts, "Punkte gesamt"),
        stat_cell(final_rank, "Tabellenplatz"),
        stat_cell(n_exact, "Exakte Tipps"),
        stat_cell(len(md_wins), "Spieltagsiege"),
    ]], colWidths=[4.1*cm]*4)
    story.append(stats_grid)

    # Tipp-Stil
    if insights:
        story.append(Paragraph("🎲 Dein Tipp-Stil", h2))
        story.append(Paragraph(
            f"Dein häufigster Tipp war <b>{insights['most_common_tip']}</b> "
            f"({insights['most_common_count']}× = {insights['most_common_pct']}% deiner Tipps).",
            body))
        story.append(Paragraph(
            f"Tendenz-Verteilung: 🏠 {insights['tendency_pct']['home']}% Heim · "
            f"⚖ {insights['tendency_pct']['draw']}% Unentschieden · "
            f"✈ {insights['tendency_pct']['away']}% Auswärts",
            body))
        story.append(Paragraph(
            f"Im Schnitt erzielst du <b>{insights['avg_points_per_match']} Punkte pro Spiel</b>.",
            body))

        if insights.get("best"):
            b = insights["best"]
            story.append(Spacer(1, 4))
            story.append(Paragraph("🌟 <b>Glanz-Tipp der Saison</b>", body))
            story.append(Paragraph(
                f"{b.match.home_team.name} {b.match.home_score}:{b.match.away_score} "
                f"{b.match.away_team.name} — Tipp: {b.home_tip}:{b.away_tip} → "
                f"<b>+{b.points} Punkte</b>", body))

        if insights.get("worst"):
            w = insights["worst"]
            story.append(Spacer(1, 4))
            story.append(Paragraph("😅 <b>Daneben gegriffen</b>", body))
            story.append(Paragraph(
                f"{w.match.home_team.name} {w.match.home_score}:{w.match.away_score} "
                f"{w.match.away_team.name} — Tipp: {w.home_tip}:{w.away_tip}", body))

    # Spieltagsiege im Detail
    if md_wins:
        story.append(Paragraph("🏆 Gewonnene Spieltage", h2))
        rows = [["Spieltag", "Punkte", "Exakte Tipps", "Status"]]
        for w in sorted(md_wins, key=lambda x: x.matchday):
            rows.append([
                f"ST {w.matchday}",
                str(w.points),
                str(w.exact_count),
                "Geteilt 🤝" if w.is_shared else "Solo 🏆",
            ])
        t = Table(rows, colWidths=[3*cm, 3*cm, 3.5*cm, 4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), teal),
            ("TEXTCOLOR", (0,0), (-1,0), white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,0), 10),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [white, bg_card]),
            ("BOX", (0,0), (-1,-1), 0.5, HexColor("#cbd5e1")),
            ("INNERGRID", (0,0), (-1,-1), 0.3, HexColor("#cbd5e1")),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(t)

    # Badges
    if badges:
        story.append(Paragraph(f"🏅 Erspielte Auszeichnungen ({len(badges)})", h2))
        for ub in badges:
            b = ub.badge
            story.append(Paragraph(
                f"<b>{b.icon} {b.name}</b> — <font color='#64748b'>{b.description}</font>",
                body))

    # Footer
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"<font color='#94a3b8' size='8'>Erstellt am {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} · "
        f"Wulmstörper Tipprunde · Saison 2025/26</font>", body))

    doc.build(story)
    buf.seek(0)
    return buf


def auto_migrate_schema():
    """Fügt fehlende Spalten/Tabellen zur SQLite-DB hinzu, ohne Datenverlust.
    Wird beim App-Start aufgerufen, damit alte DBs automatisch aktualisiert werden.

    Hinweis: Funktioniert nur für SQLite (für PostgreSQL/MySQL bitte Flask-Migrate
    oder das schema-update-Script nutzen)."""
    from sqlalchemy import inspect, text

    engine = db.engine
    if not engine.url.drivername.startswith("sqlite"):
        return  # Nur SQLite; andere DBs sollten Flask-Migrate nutzen

    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    # Definition aller erwarteten Spalten je Tabelle (Name → SQLite-Typ + Default)
    schema_updates = {
        "users": [
            ("full_name",       "VARCHAR(120)",       "NULL"),
            ("favorite_team_id","INTEGER",            "NULL"),
            ("phone",           "VARCHAR(40)",        "NULL"),
            ("show_full_name",  "BOOLEAN",            "1"),
            ("has_paid",        "BOOLEAN",            "0"),
            ("paid_at",         "DATETIME",           "NULL"),
            ("paid_note",       "VARCHAR(200)",       "NULL"),
            ("push_subscription","TEXT",              "NULL"),
            ("whatsapp_phone",  "VARCHAR(30)",        "NULL"),
            ("whatsapp_apikey", "VARCHAR(20)",        "NULL"),
        ],
        "matches": [
            ("competition_id",  "INTEGER",            "1"),
            ("is_live",         "BOOLEAN",            "0"),
            ("minute",          "INTEGER",            "NULL"),
            ("events",          "TEXT",               "NULL"),
        ],
        "predictions": [
            ("created_at",      "DATETIME",           "NULL"),
            ("updated_at",      "DATETIME",           "NULL"),
        ],
        "badges": [
            ("color",           "VARCHAR(20)",        "'#fbbf24'"),
            ("trigger_type",    "VARCHAR(30)",        "'manual'"),
            ("threshold",       "INTEGER",            "0"),
            ("active",          "BOOLEAN",            "1"),
            ("created_at",      "DATETIME",           "NULL"),
        ],
        "special_questions": [
            ("description",     "VARCHAR(500)",       "NULL"),
            ("answer_type",     "VARCHAR(20)",        "'text'"),
            ("number_min",      "INTEGER",            "NULL"),
            ("number_max",      "INTEGER",            "NULL"),
            ("multi_count",     "INTEGER",            "1"),
            ("season",          "VARCHAR(20)",        "'2025/26'"),
            ("created_at",      "DATETIME",           "NULL"),
        ],
        "special_predictions": [
            ("created_at",      "DATETIME",           "NULL"),
            ("updated_at",      "DATETIME",           "NULL"),
        ],
    }

    added = []
    with engine.begin() as conn:
        for table_name, columns in schema_updates.items():
            if table_name not in existing_tables:
                continue   # Tabelle wird gleich von db.create_all() angelegt
            existing_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col_name, col_type, col_default in columns:
                if col_name in existing_cols:
                    continue
                default_clause = f" DEFAULT {col_default}" if col_default != "NULL" else ""
                ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {col_type}{default_clause}'
                try:
                    conn.execute(text(ddl))
                    added.append(f"{table_name}.{col_name}")
                except Exception as e:
                    current_app.logger.warning(
                        f"Auto-Migration: konnte '{ddl}' nicht ausführen: {e}"
                    )

    if added:
        current_app.logger.info(f"✅ Auto-Migration: {len(added)} Spalten ergänzt: {', '.join(added)}")

    # NULL-Werte in BOOLEAN-Spalten korrigieren (alte Zeilen,
    # falls DEFAULT bei ALTER TABLE nicht griff)
    null_fixes = [
        ("users", "show_full_name", 1),
        ("users", "has_paid", 0),
        ("badges", "active", 1),
        ("predictions", "joker", 0),
    ]
    with engine.begin() as conn:
        for tbl, col, default in null_fixes:
            try:
                conn.execute(text(
                    f'UPDATE "{tbl}" SET "{col}" = :d WHERE "{col}" IS NULL'
                ), {"d": default})
            except Exception:
                pass  # Spalte existiert evtl. nicht oder andere DB


def get_open_matches_for_user(user, max_hours=72):
    """Findet alle 'scheduled' Matches in den nächsten `max_hours` Stunden,
    für die der User noch KEINEN Tipp abgegeben hat.
    Liefert Liste aufsteigend nach Anpfiff sortiert."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max_hours)
    matches = Match.query.filter(
        Match.status == "scheduled",
        Match.kickoff > now,
        Match.kickoff <= horizon,
    ).order_by(Match.kickoff.asc()).all()
    if not matches:
        return []
    tipped_ids = {p.match_id for p in user.predictions.all()}
    return [m for m in matches if m.id not in tipped_ids]


def get_current_matchday():
    """Liefert den aktuell relevanten Spieltag.
    Logik:
      1. Gibt es noch einen ungespielten Spieltag (mit mind. einem 'scheduled'/'live' Match)?
         → niedrigster solcher Spieltag.
      2. Sonst: höchster vorhandener Spieltag (Saison-Ende).
      3. Sonst: 1.
    """
    # Spieltage mit mindestens einem offenen Spiel
    open_md = db.session.query(Match.matchday).filter(
        Match.status.in_(["scheduled", "live"])
    ).order_by(Match.matchday.asc()).first()
    if open_md:
        return open_md[0]
    last_md = db.session.query(Match.matchday).order_by(Match.matchday.desc()).first()
    return last_md[0] if last_md else 1


def seed_teams_if_empty():
    if Team.query.count() == 0:
        for name, short, ext_id, logo, color in BUNDESLIGA_TEAMS:
            db.session.add(Team(
                name=name, short_name=short, external_id=ext_id, logo=logo, color=color
            ))
        db.session.commit()


def _purge_demo_matches():
    """Loescht alle Demo-Matches (ohne external_id), inkl. der zugehoerigen
    Predictions/Comments. Wird vor einem API-Sync aufgerufen, damit API-Daten
    nicht parallel zu den Seed-Daten existieren."""
    from models import Prediction, Comment
    demo = Match.query.filter(Match.external_id.is_(None)).all()
    count = len(demo)
    if count == 0:
        return 0
    demo_ids = [m.id for m in demo]
    Prediction.query.filter(Prediction.match_id.in_(demo_ids)).delete(synchronize_session=False)
    Comment.query.filter(Comment.match_id.in_(demo_ids)).delete(synchronize_session=False)
    Match.query.filter(Match.id.in_(demo_ids)).delete(synchronize_session=False)
    db.session.commit()
    return count


def _fd_request(path, ttl_seconds=30):
    """Wrapper für football-data.org-Requests mit Token + Caching.
    
    Cache läuft über die settings-Tabelle (DB-persistent, Multi-Worker-safe).
    Wir umgehen damit das harte 10-Calls/Min-Limit komplett.
    
    Liefert (data_dict, error_msg) zurück.
    """
    token = get_setting("football_data_token", current_app.config["FOOTBALL_DATA_TOKEN"])
    if not token:
        return None, "Kein football-data.org-Token gesetzt (Admin → Einstellungen)."

    cache_key = f"fd_cache:{path}"
    cached = get_setting(cache_key)
    now_ts = datetime.now(timezone.utc).timestamp()
    if cached and isinstance(cached, dict):
        ts = cached.get("ts", 0)
        if now_ts - ts < ttl_seconds:
            return cached.get("data"), None

    url = f"{current_app.config['FOOTBALL_DATA_BASE']}{path}"
    headers = {"X-Auth-Token": token}
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except requests.exceptions.RequestException as e:
        # Bei Netzwerkfehler: alten Cache liefern, falls vorhanden
        if cached and isinstance(cached, dict):
            return cached.get("data"), None
        return None, f"Netzwerkfehler: {e}"

    if r.status_code == 429:
        # Rate-Limit überschritten → letzten Cache verwenden, falls da
        if cached and isinstance(cached, dict):
            return cached.get("data"), None
        return None, "API-Rate-Limit erreicht (10 Calls/Min). Bitte später erneut versuchen."

    if r.status_code != 200:
        if cached and isinstance(cached, dict):
            return cached.get("data"), None
        return None, f"API-Fehler {r.status_code} (Token/Quota prüfen)"

    try:
        data = r.json()
    except ValueError:
        return None, "API lieferte ungültiges JSON"

    set_setting(cache_key, {"ts": now_ts, "data": data})
    return data, None


def sync_with_football_data():
    """Hauptsync gegen football-data.org (Spielplan + Ergebnisse)."""
    season = current_app.config["SEASON"]
    comp = current_app.config["COMPETITION"]
    # Cache umgehen beim manuellen Sync (kurze TTL → frische Daten)
    data, err = _fd_request(f"/competitions/{comp}/matches?season={season}", ttl_seconds=5)
    if err:
        return {"ok": False, "msg": err}

    purged = _purge_demo_matches()
    other_purged = _purge_external_other_than("footballdata")
    received = len(data.get("matches", []))
    res = _process_football_data(data)
    if not res.get("ok"):
        return res

    # Hinweis ergänzen
    parts = [f"{received} von API erhalten", res["msg"]]
    if purged:
        parts.insert(0, f"{purged} Demo entfernt")
    if other_purged:
        parts.insert(0, f"{other_purged} OpenLigaDB-Spiele entfernt")
    res["msg"] = ". ".join(parts)
    return res


def fetch_live_standings():
    """Live-Tabelle direkt von football-data.org (Cache: 60s).
    Liefert (rows, error_msg) — rows hat dasselbe Format wie compute_live_standings()."""
    season = current_app.config["SEASON"]
    comp = current_app.config["COMPETITION"]
    data, err = _fd_request(f"/competitions/{comp}/standings?season={season}", ttl_seconds=60)
    if err or not data:
        return None, err or "Keine Daten erhalten"

    # football-data.org liefert: standings: [{type:"TOTAL", table:[{position, team, playedGames, won, draw, lost, points, goalsFor, goalsAgainst, goalDifference, form}, ...]}]
    standings_arr = data.get("standings", [])
    total = next((s for s in standings_arr if s.get("type") == "TOTAL"), None)
    if not total:
        return None, "Keine TOTAL-Tabelle in API-Antwort"

    # Map external_id → unser Team
    teams_by_ext = {t.external_id: t for t in Team.query.all() if t.external_id}

    rows = []
    for entry in total.get("table", []):
        ext_id = entry.get("team", {}).get("id")
        team_obj = teams_by_ext.get(ext_id)
        if not team_obj:
            # Falls API ein Team liefert das wir nicht kennen → minimal-Eintrag
            class _PseudoTeam:
                pass
            team_obj = _PseudoTeam()
            team_obj.id = -1
            team_obj.name = entry.get("team", {}).get("name", "?")
            team_obj.short_name = entry.get("team", {}).get("tla", "???")
            team_obj.logo = entry.get("team", {}).get("crest", "")

        rows.append({
            "rank": entry.get("position", 0),
            "team": team_obj,
            "played": entry.get("playedGames", 0),
            "won": entry.get("won", 0),
            "drawn": entry.get("draw", 0),
            "lost": entry.get("lost", 0),
            "goals_for": entry.get("goalsFor", 0),
            "goals_against": entry.get("goalsAgainst", 0),
            "goal_diff": entry.get("goalDifference", 0),
            "points": entry.get("points", 0),
            "form": entry.get("form", ""),  # z.B. "W,D,L,W,W"
        })
    return rows, None


def fetch_live_match_updates(matchday=None):
    """Holt aktuelle Match-Daten direkt von der API und aktualisiert nur Scores/Status.
    Schnell + günstig (Cache 30s)."""
    season = current_app.config["SEASON"]
    comp = current_app.config["COMPETITION"]
    path = f"/competitions/{comp}/matches?season={season}"
    if matchday:
        path += f"&matchday={matchday}"
    data, err = _fd_request(path, ttl_seconds=30)
    if err or not data:
        return {"ok": False, "msg": err or "Keine Daten"}

    updated = 0
    live = 0
    for m in data.get("matches", []):
        raw_id = m.get("id")
        ext_id = f"fd:{raw_id}"
        # Suche per Prefix-ID, fallback auf alte int-Variante
        match = (
            Match.query.filter_by(external_id=ext_id).first()
            or Match.query.filter_by(external_id=str(raw_id)).first()
        )
        if not match:
            continue
        status_raw = m.get("status", "SCHEDULED")
        new_status = (
            "finished" if status_raw == "FINISHED"
            else "live" if status_raw in ("IN_PLAY", "PAUSED", "LIVE")
            else "scheduled"
        )
        new_home = m.get("score", {}).get("fullTime", {}).get("home")
        new_away = m.get("score", {}).get("fullTime", {}).get("away")
        if new_status == "live":
            live += 1
        if (match.status != new_status or match.home_score != new_home or match.away_score != new_away):
            match.status = new_status
            match.home_score = new_home
            match.away_score = new_away
            updated += 1
    if updated:
        db.session.commit()
        recalculate_all_points()
        check_and_award_badges()
    return {"ok": True, "updated": updated, "live": live}


def _process_football_data(data):
    """Verarbeitet football-data.org-API-Antwort. Eindeutige IDs mit 'fd:'-Prefix.
    Beim Matching: zuerst 'fd:<id>', fallback auf alte int-id ohne Prefix."""
    created, updated, skipped = 0, 0, 0
    for m in data.get("matches", []):
        raw_id = m["id"]
        ext_id = f"fd:{raw_id}"
        home_ext = m["homeTeam"]["id"]
        away_ext = m["awayTeam"]["id"]
        try:
            kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            skipped += 1
            continue
        matchday = m.get("matchday", 1)

        # Team-Matching: external_id (Team.external_id ist Integer)
        home = Team.query.filter_by(external_id=home_ext).first()
        away = Team.query.filter_by(external_id=away_ext).first()
        # Fallback: über Namen (wenn Team-IDs sich geändert haben)
        if not home:
            home = _resolve_team_by_name(m["homeTeam"].get("name", ""))
        if not away:
            away = _resolve_team_by_name(m["awayTeam"].get("name", ""))
        if not home or not away or home.id == away.id:
            current_app.logger.warning(
                f"FD-Match übersprungen: {m['homeTeam'].get('name')} ({home_ext}) "
                f"vs {m['awayTeam'].get('name')} ({away_ext})"
            )
            skipped += 1
            continue

        home_score = m.get("score", {}).get("fullTime", {}).get("home")
        away_score = m.get("score", {}).get("fullTime", {}).get("away")
        status_raw = m.get("status", "SCHEDULED")
        status = (
            "finished" if status_raw == "FINISHED"
            else "live" if status_raw in ("IN_PLAY", "PAUSED", "LIVE")
            else "scheduled"
        )

        # Suche: zuerst nach neuem Prefix, fallback alte int-ID
        match = (
            Match.query.filter_by(external_id=ext_id).first()
            or Match.query.filter_by(external_id=str(raw_id)).first()
        )
        if match:
            match.external_id = ext_id  # auf neue Variante normalisieren
            match.matchday = matchday
            match.home_team_id = home.id
            match.away_team_id = away.id
            match.kickoff = kickoff
            match.home_score = home_score
            match.away_score = away_score
            match.status = status
            updated += 1
        else:
            db.session.add(Match(
                external_id=ext_id, matchday=matchday,
                home_team_id=home.id, away_team_id=away.id,
                kickoff=kickoff, home_score=home_score,
                away_score=away_score, status=status,
            ))
            created += 1
    db.session.commit()
    recalculate_all_points()
    check_and_award_badges()
    parts = [f"{created} angelegt", f"{updated} aktualisiert"]
    if skipped:
        parts.append(f"{skipped} übersprungen")
    return {"ok": True, "msg": ", ".join(parts)}


# OpenLigaDB-Teamname → unsere short_name. Robustes explizites Mapping
# verhindert "1. FC ..." matched alle "1. FC"-Teams.
OPENLIGADB_TEAM_MAP = {
    "fc bayern münchen": "FCB",
    "borussia dortmund": "BVB",
    "bayer 04 leverkusen": "B04",
    "rb leipzig": "RBL",
    "vfb stuttgart": "VFB",
    "eintracht frankfurt": "SGE",
    "vfl wolfsburg": "WOB",
    "borussia mönchengladbach": "BMG",
    "sc freiburg": "SCF",
    "1. fc union berlin": "FCU",
    "tsg hoffenheim": "TSG",
    "tsg 1899 hoffenheim": "TSG",
    "1. fsv mainz 05": "M05",
    "fsv mainz 05": "M05",
    "fc augsburg": "FCA",
    "sv werder bremen": "SVW",
    "werder bremen": "SVW",
    "1. fc heidenheim": "FCH",
    "1. fc heidenheim 1846": "FCH",
    "fc st. pauli": "STP",
    "st. pauli": "STP",
    "hamburger sv": "HSV",
    "1. fc köln": "KOE",
}


def _resolve_team_by_name(api_name):
    """Robustes Team-Matching gegen unsere DB.
    1. Exakte Mapping-Tabelle (case-insensitive)
    2. Fallback: exakter Name in DB
    3. Fallback: Team.name enthält api_name oder umgekehrt
    """
    if not api_name:
        return None
    norm = api_name.strip().lower()
    short = OPENLIGADB_TEAM_MAP.get(norm)
    if short:
        team = Team.query.filter_by(short_name=short).first()
        if team:
            return team
    # Exakter Name?
    team = Team.query.filter(db.func.lower(Team.name) == norm).first()
    if team:
        return team
    # Best-effort: enthält
    team = Team.query.filter(Team.name.ilike(f"%{api_name}%")).first()
    if team:
        return team
    # Reverse: api_name enthaelt unseren Namen
    for t in Team.query.all():
        if t.name.lower() in norm or t.short_name.lower() in norm:
            return t
    return None


def sync_with_openligadb():
    """Fallback: OpenLigaDB. Liefert die aktuelle Saison (alle 34 Spieltage)."""
    season = "2025"
    league = "bl1"
    url = f"{current_app.config['OPENLIGADB_BASE']}/getmatchdata/{league}/{season}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return {"ok": False, "msg": f"OpenLigaDB-Fehler {r.status_code}"}
        matches = r.json()
    except Exception as e:
        return {"ok": False, "msg": str(e)}

    purged = _purge_demo_matches()
    # Vor OpenLigaDB-Sync: andere fremde Daten (z.B. von football-data.org)
    # auch loeschen, damit keine Mischung entsteht
    other_purged = _purge_external_other_than("openligadb")

    created, updated, skipped = 0, 0, 0
    for m in matches:
        ext_id = f"oldb:{m['matchID']}"  # Quelle als Prefix → Konflikt mit FD ausgeschlossen
        try:
            kickoff = datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", ""))
        except (KeyError, ValueError):
            skipped += 1
            continue
        matchday = m.get("group", {}).get("groupOrderID")
        if not matchday:
            skipped += 1
            continue

        home = _resolve_team_by_name(m["team1"]["teamName"])
        away = _resolve_team_by_name(m["team2"]["teamName"])
        if not home or not away:
            current_app.logger.warning(
                f"OpenLigaDB-Team nicht gefunden: '{m['team1']['teamName']}' "
                f"oder '{m['team2']['teamName']}'"
            )
            skipped += 1
            continue
        if home.id == away.id:
            skipped += 1
            continue

        results = m.get("matchResults", [])
        end_result = next((r for r in results if r.get("resultName") == "Endergebnis"), None)
        is_finished = m.get("matchIsFinished", False)
        home_score = end_result["pointsTeam1"] if end_result else None
        away_score = end_result["pointsTeam2"] if end_result else None
        status = "finished" if is_finished else "scheduled"

        match = Match.query.filter_by(external_id=ext_id).first()
        if match:
            match.kickoff = kickoff
            match.home_team_id = home.id
            match.away_team_id = away.id
            match.matchday = matchday
            match.home_score = home_score
            match.away_score = away_score
            match.status = status
            updated += 1
        else:
            db.session.add(Match(
                external_id=ext_id, matchday=matchday,
                home_team_id=home.id, away_team_id=away.id,
                kickoff=kickoff, home_score=home_score,
                away_score=away_score, status=status,
            ))
            created += 1
    db.session.commit()
    recalculate_all_points()
    check_and_award_badges()
    parts = [f"OpenLigaDB: {created} angelegt, {updated} aktualisiert"]
    if skipped:
        parts.append(f"{skipped} übersprungen")
    if purged:
        parts.insert(0, f"{purged} Demo entfernt")
    if other_purged:
        parts.insert(0, f"{other_purged} football-data Spiele entfernt")
    return {"ok": True, "msg": ". ".join(parts)}


def _purge_external_other_than(source):
    """Loescht alle Matches einer ANDEREN externen Quelle.
    Verhindert Duplikate beim Wechsel zwischen football-data.org und OpenLigaDB.
    
    source = 'openligadb' → loescht alle, deren external_id NICHT mit 'oldb:' beginnt
    source = 'footballdata' → loescht alle mit 'oldb:'-Prefix
    """
    from models import Prediction, Comment
    if source == "openligadb":
        # Alles, was external_id ohne 'oldb:'-Prefix hat (= football-data) loeschen
        # external_id koennte int oder string sein - wir filtern auf NULL exclusive + nicht-oldb:
        to_delete = Match.query.filter(
            Match.external_id.isnot(None),
            ~Match.external_id.like("oldb:%"),
        ).all()
    else:
        to_delete = Match.query.filter(
            Match.external_id.like("oldb:%"),
        ).all()
    if not to_delete:
        return 0
    ids = [m.id for m in to_delete]
    Prediction.query.filter(Prediction.match_id.in_(ids)).delete(synchronize_session=False)
    Comment.query.filter(Comment.match_id.in_(ids)).delete(synchronize_session=False)
    Match.query.filter(Match.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return len(to_delete)


def sync_results():
    """Synchronisiert Spiele/Ergebnisse.

    Reihenfolge:
    1. football-data.org (schnell, präzise, Token nötig)
    2. Auto-Fill aus OpenLigaDB, falls FD unvollständig ist
       (Free-Tier liefert oft nur die letzten ~10 Spieltage)
    3. OpenLigaDB-Komplett-Fallback, wenn FD ganz fehlschlägt
    """
    token = get_setting("football_data_token", current_app.config["FOOTBALL_DATA_TOKEN"])
    if not token:
        # Kein Token → direkt OpenLigaDB
        return sync_with_openligadb()

    res = sync_with_football_data()
    if not res["ok"]:
        # FD komplett fehlgeschlagen → OpenLigaDB versuchen
        fallback = sync_with_openligadb()
        if fallback["ok"]:
            fallback["msg"] = (
                f"⚠️ football-data.org-Fehler ({res['msg']}). "
                "OpenLigaDB-Fallback genutzt. " + fallback["msg"]
            )
            return fallback
        return res

    # FD war erfolgreich → prüfen, ob alle 306 Matches da sind
    total_matches = Match.query.count()
    expected = 18 * 17  # 18 Teams × 34 Spieltage / 2 = 306
    if total_matches < expected:
        missing = expected - total_matches
        # Ergänzen aus OpenLigaDB - aber nur die fehlenden Spieltage!
        fill_msg = _fill_missing_from_openligadb()
        res["msg"] += (
            f". ⚠️ FD-Free-Tier liefert nur {total_matches} von {expected} Spielen "
            f"(fehlen {missing}). {fill_msg}"
        )
    return res


def _fill_missing_from_openligadb():
    """Ergaenzt fehlende Spieltage aus OpenLigaDB.
    Wird aufgerufen, wenn football-data.org Free-Tier unvollständig ist.
    Wichtig: nur Spieltage hinzufuegen, die NICHT schon von FD da sind."""
    season = "2025"
    league = "bl1"
    url = f"{current_app.config['OPENLIGADB_BASE']}/getmatchdata/{league}/{season}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return f"OpenLigaDB-Fehler {r.status_code} - kein Auto-Fill möglich."
        all_matches = r.json()
    except Exception as e:
        return f"OpenLigaDB unerreichbar ({e}) - kein Auto-Fill möglich."

    # Welche Spieltage sind in unserer DB schon vollständig (=9 Matches)?
    from sqlalchemy import func
    md_counts = dict(
        db.session.query(Match.matchday, func.count(Match.id))
        .group_by(Match.matchday).all()
    )

    added = 0
    for m in all_matches:
        md = m.get("group", {}).get("groupOrderID")
        if not md:
            continue
        # Spieltag schon komplett? → skip
        if md_counts.get(md, 0) >= 9:
            continue

        ext_id = f"oldb:{m['matchID']}"
        # Pruefen ob das spezifische Spiel schon (irgendwie) in DB ist
        if Match.query.filter_by(external_id=ext_id).first():
            continue

        try:
            kickoff = datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", ""))
        except (KeyError, ValueError):
            continue

        home = _resolve_team_by_name(m["team1"]["teamName"])
        away = _resolve_team_by_name(m["team2"]["teamName"])
        if not home or not away or home.id == away.id:
            continue

        # Gibt es schon ein Match mit gleicher Konstellation an diesem Spieltag?
        # (z.B. via football-data.org schon importiert)
        exists = Match.query.filter_by(
            matchday=md,
            home_team_id=home.id,
            away_team_id=away.id,
        ).first()
        if exists:
            continue  # nicht doppelt anlegen

        results = m.get("matchResults", [])
        end_result = next((r for r in results if r.get("resultName") == "Endergebnis"), None)
        is_finished = m.get("matchIsFinished", False)
        home_score = end_result["pointsTeam1"] if end_result else None
        away_score = end_result["pointsTeam2"] if end_result else None
        status = "finished" if is_finished else "scheduled"

        db.session.add(Match(
            external_id=ext_id, matchday=md,
            home_team_id=home.id, away_team_id=away.id,
            kickoff=kickoff, home_score=home_score,
            away_score=away_score, status=status,
        ))
        added += 1

    db.session.commit()
    if added:
        recalculate_all_points()
        check_and_award_badges()
        return f"{added} fehlende Spiele aus OpenLigaDB ergänzt."
    return "Kein Auto-Fill nötig."


# -------------------------------------------------------- Demo Daten -
def seed_demo_matches():
    """Erstellt 6 Spieltage mit jeweils 9 Spielen, falls leer."""
    if Match.query.count() > 0:
        return
    teams = Team.query.all()
    if len(teams) < 18:
        return
    
    from models import Competition
    comp = Competition.query.filter_by(code="BL1").first()
    if not comp:
        comp = Competition.query.first()
    comp_id = comp.id if comp else 1

    import random
    base_date = datetime.now(timezone.utc) - timedelta(days=14)
    for md in range(1, 35):
        random.shuffle(teams)
        for i in range(0, 18, 2):
            home, away = teams[i], teams[i + 1]
            kickoff = base_date + timedelta(days=(md - 1) * 7, hours=15 + (i % 4))
            status = "finished" if md <= 2 else "scheduled"
            home_s = random.randint(0, 4) if status == "finished" else None
            away_s = random.randint(0, 4) if status == "finished" else None
            db.session.add(Match(
                competition_id=comp_id,
                matchday=md, home_team_id=home.id, away_team_id=away.id,
                kickoff=kickoff, status=status,
                home_score=home_s, away_score=away_s,
            ))
    db.session.commit()


# ---------------------------------------------------------- Badges -
# Default-Badges mit Trigger-Konfiguration
# (code, name, description, icon, color, trigger_type, threshold)
DEFAULT_BADGES = [
    ("first_tip",    "Tipp-Premiere",  "Ersten Tipp abgegeben",                  "🎯", "#10b981", "first_tip",    1),
    ("loyal",        "Treuer Tipper",  "30 Tipps abgegeben",                     "🏆", "#f59e0b", "tips_count",   30),
    ("veteran",      "Veteran",        "100 Tipps abgegeben",                    "🎖", "#8b5cf6", "tips_count",  100),
    ("100_points",   "Hundertschaft",  "100 Punkte erreicht",                    "💯", "#ef4444", "total_points",100),
    ("500_points",   "Halbtausend",    "500 Punkte erreicht",                    "🚀", "#3b82f6", "total_points",500),
    ("sharp_shooter","Scharfschütze",  "10 exakte Tipps",                        "🎯", "#ec4899", "exact_count",  10),
    ("joker_master", "Joker-Meister",  "Joker mit exaktem Tipp eingelöst",       "⚡", "#fbbf24", "joker_exact",   0),
    ("perfect_day",  "Tagessieger",    "Alle Spiele eines Spieltags exakt",      "👑", "#fbbf24", "perfect_day",   0),
    ("md_winner_1",  "Spieltagsieger", "Erster Spieltagsieg",                    "🏆", "#14b8a6", "matchday_winner", 1),
    ("md_winner_3",  "Triple-Sieger",  "3 Spieltage gewonnen",                   "🥇", "#f59e0b", "matchday_winner", 3),
    ("md_winner_5",  "Serien-Sieger",  "5 Spieltage gewonnen",                   "🔥", "#ef4444", "matchday_winner", 5),
    ("champion",     "Saisonchampion", "Sondertrophäe vom Admin",                "🏅", "#fbbf24", "manual",        0),
    ("season_mvp",   "MVP der Saison", "Vom Admin handverlesen",                 "⭐", "#fde047", "manual",        0),
]


def seed_badges():
    if Badge.query.count() == 0:
        for code, name, desc, icon, color, ttype, thresh in DEFAULT_BADGES:
            db.session.add(Badge(
                code=code, name=name, description=desc, icon=icon,
                color=color, trigger_type=ttype, threshold=thresh,
            ))
        db.session.commit()


# Default-Preise für die Gewinner-Seite
DEFAULT_PRIZES = [
    # (rank, title, description, icon, color, amount, detail, sort_order)
    (1, "1. Platz", "Saisonsieger des Tippspiels", "🥇", "#fbbf24",
     "50% des Potts", "Der Hauptgewinn geht an den besten Tipper der Saison.", 1),
    (2, "2. Platz", "Vize-Champion", "🥈", "#9ca3af",
     "30% des Potts", "Auch der zweite Platz wird belohnt.", 2),
    (3, "3. Platz", "Bronze-Rang", "🥉", "#b45309",
     "20% des Potts", "Top 3 ist nicht ohne!", 3),
    (0, "Trostpreis", "Letzter Platz / Schlechtester Tipper", "🍺", "#3b82f6",
     "Eine Runde Bier", "Damit auch der Letzte was vom Tippspiel hat.", 99),
]


def seed_prizes():
    """Erstellt Standard-Preise beim ersten Start."""
    from models import Prize
    if Prize.query.count() == 0:
        for rank, title, desc, icon, color, amount, detail, order in DEFAULT_PRIZES:
            db.session.add(Prize(
                rank=rank, title=title, description=desc,
                icon=icon, color=color, amount=amount, detail=detail,
                sort_order=order,
            ))
        db.session.commit()


def award_badge(user, code_or_badge):
    """Vergibt Badge an User. Akzeptiert Code-String oder Badge-Objekt."""
    badge = (
        code_or_badge if isinstance(code_or_badge, Badge)
        else Badge.query.filter_by(code=code_or_badge).first()
    )
    if not badge:
        return False
    existing = UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first()
    if existing:
        return False
    db.session.add(UserBadge(user_id=user.id, badge_id=badge.id))
    db.session.commit()
    return True


def revoke_badge(user, badge):
    """Entzieht ein Badge wieder."""
    ub = UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first()
    if ub:
        db.session.delete(ub)
        db.session.commit()
        return True
    return False


def _user_qualifies(user, badge):
    """Prüft, ob ein User die Bedingungen eines Badges erfüllt."""
    t = badge.trigger_type
    threshold = badge.threshold or 0

    if t == "manual":
        return False  # nie automatisch

    if t == "first_tip":
        return user.predictions.count() >= 1

    if t == "tips_count":
        return user.predictions.count() >= max(1, threshold)

    if t == "total_points":
        return user.total_points() >= max(1, threshold)

    if t == "exact_count":
        from utils import get_setting as _gs
        exact_pts = _gs("points_exact", 4)
        n = Prediction.query.filter(
            Prediction.user_id == user.id,
            Prediction.points >= exact_pts,
        ).count()
        return n >= max(1, threshold)

    if t == "joker_exact":
        return Prediction.query.filter_by(user_id=user.id, joker=True).join(Match).filter(
            Prediction.home_tip == Match.home_score,
            Prediction.away_tip == Match.away_score,
            Match.status == "finished",
        ).first() is not None

    if t == "perfect_day":
        # Pro Spieltag: hat User für alle 9 Matches exakt getippt?
        # Performance: Spieltage durchgehen
        from utils import get_setting as _gs
        exact_pts = _gs("points_exact", 4)
        finished_mds = db.session.query(Match.matchday).filter_by(status="finished").distinct().all()
        for (md,) in finished_mds:
            md_matches = Match.query.filter_by(matchday=md, status="finished").all()
            if not md_matches:
                continue
            preds = Prediction.query.filter(
                Prediction.user_id == user.id,
                Prediction.match_id.in_([m.id for m in md_matches]),
            ).all()
            # Alle Matches getippt + alle exakt?
            if len(preds) == len(md_matches) and all(
                (p.points or 0) >= exact_pts for p in preds
            ):
                return True
        return False

    if t == "matchday_winner":
        # User hat mind. 'threshold' Spieltage gewonnen (geteilte Siege zaehlen)
        wins = MatchdayWinner.query.filter_by(user_id=user.id).count()
        return wins >= max(1, threshold)

    return False


def check_and_award_badges():
    """Geht alle aktiven Badges durch und vergibt sie automatisch.
    'manual'-Badges werden hier ignoriert."""
    badges = Badge.query.filter_by(active=True).all()
    users = User.query.all()
    for badge in badges:
        if badge.trigger_type == "manual":
            continue
        for user in users:
            if _user_qualifies(user, badge):
                award_badge(user, badge)


# ----------------------------------------------------- Tabelle / Stats -
def classify_prediction(prediction, match):
    """Liefert ('exact'|'diff'|'tendency'|'wrong'|'pending') zurueck."""
    if match.status != "finished" or match.home_score is None or match.away_score is None:
        return "pending"
    h, a = prediction.home_tip, prediction.away_tip
    rh, ra = match.home_score, match.away_score
    if h == rh and a == ra:
        return "exact"
    if (h - a) == (rh - ra):
        return "diff"
    if (h > a and rh > ra) or (h < a and rh < ra) or (h == a and rh == ra):
        return "tendency"
    return "wrong"


def get_user_stats(user, matchday=None):
    """Detaillierte Statistik mit exact/diff/tendency/wrong + Punkten."""
    from flask import session
    from models import Competition
    active_code = session.get("competition_code") if session else None
    comp = None
    if active_code:
        comp = Competition.query.filter_by(code=active_code, is_active=True).first()
        
    q = Prediction.query.filter_by(user_id=user.id)
    if comp:
        q = q.join(Match).filter(Match.competition_id == comp.id)
        if matchday:
            q = q.filter(Match.matchday == matchday)
    else:
        if matchday:
            q = q.join(Match).filter(Match.matchday == matchday)
    preds = q.all()

    counters = {"exact": 0, "diff": 0, "tendency": 0, "wrong": 0, "pending": 0}
    total_pts = 0
    joker_used = 0
    sp_pts = 0

    for p in preds:
        kind = classify_prediction(p, p.match)
        counters[kind] += 1
        total_pts += (p.points or 0)
        if p.joker:
            joker_used += 1

    # Sonderpunkte einrechnen (nur bei Gesamttabelle)
    if not matchday:
        sp = SpecialPrediction.query.filter_by(user_id=user.id).all()
        sp_pts = sum(s.points or 0 for s in sp)

    finished = counters["exact"] + counters["diff"] + counters["tendency"] + counters["wrong"]
    quote = round((counters["exact"] / finished) * 100) if finished else 0

    return {
        "user": user,
        "points": total_pts + sp_pts,
        "match_points": total_pts,
        "special_points": sp_pts,
        "tips": len(preds),
        "exact": counters["exact"],
        "diff": counters["diff"],
        "tendency": counters["tendency"],
        "wrong": counters["wrong"],
        "pending": counters["pending"],
        "joker_used": joker_used,
        "exact_quote": quote,
    }


def get_leaderboard(matchday=None):
    """Erweiterte Tabelle mit allen Tipp-Kategorien (mit Cache!)."""
    from cache import cache
    
    # 🔥 PERFORMANCE: Redis-Cache verwenden falls verfügbar
    cache_key = f"leaderboard:{matchday or 'total'}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result
    
    # Hole alle User, aber filtere inaktive Bots heraus
    all_users = User.query.all()
    active_users = []
    for u in all_users:
        # Prüfe ob es ein Bot ist und ob er aktiv ist
        if u.username.endswith('Bot'):
            is_active = get_setting(f"bot_active_{u.username}", True)
            if not is_active:
                continue  # Inaktiven Bot überspringen
        active_users.append(u)
    
    # Berechnen (teuer!)
    rows = [get_user_stats(u, matchday=matchday) for u in active_users]
    # Gleichstand: Punkte -> Exakt -> Diff -> Tendenz -> Tipps -> Username
    rows.sort(key=lambda r: (
        -r["points"], -r["exact"], -r["diff"], -r["tendency"], -r["tips"], r["user"].username
    ))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    
    # 🔥 PERFORMANCE: Ergebnis für 2 Minuten cachen
    cache.set(cache_key, rows, ttl=120)
    return rows


# ==================================================== Live-Bundesliga-Tabelle -
def compute_live_standings():
    """Berechnet die aktuelle Bundesliga-Tabelle aus allen finished Matches."""
    teams = Team.query.all()
    table = {t.id: {
        "team": t, "played": 0, "won": 0, "drawn": 0, "lost": 0,
        "goals_for": 0, "goals_against": 0, "points": 0,
    } for t in teams}

    finished = Match.query.filter_by(status="finished").all()
    for m in finished:
        if m.home_score is None or m.away_score is None:
            continue
        h = table.get(m.home_team_id)
        a = table.get(m.away_team_id)
        if not h or not a:
            continue
        h["played"] += 1
        a["played"] += 1
        h["goals_for"] += m.home_score
        h["goals_against"] += m.away_score
        a["goals_for"] += m.away_score
        a["goals_against"] += m.home_score
        if m.home_score > m.away_score:
            h["won"] += 1; h["points"] += 3
            a["lost"] += 1
        elif m.home_score < m.away_score:
            a["won"] += 1; a["points"] += 3
            h["lost"] += 1
        else:
            h["drawn"] += 1; h["points"] += 1
            a["drawn"] += 1; a["points"] += 1

    rows = list(table.values())
    for r in rows:
        r["goal_diff"] = r["goals_for"] - r["goals_against"]
    rows.sort(key=lambda r: (-r["points"], -r["goal_diff"], -r["goals_for"], r["team"].name))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def get_team_position(team_id):
    """Gibt die aktuelle Tabellenposition eines Teams zurueck."""
    standings = compute_live_standings()
    for r in standings:
        if r["team"].id == team_id:
            return r
    return None


# ============================================================== Form / H2H -
def get_team_form(team_id, limit=5):
    """Liefert die letzten n Ergebnisse eines Teams als Liste von dicts.
    Format: [{'result': 'W'|'D'|'L', 'score': '2:1', 'opponent': 'BVB', 'home': True, 'date': dt}, ...]
    """
    matches = Match.query.filter(
        Match.status == "finished",
        ((Match.home_team_id == team_id) | (Match.away_team_id == team_id)),
        Match.home_score.isnot(None),
        Match.away_score.isnot(None),
    ).order_by(Match.kickoff.desc()).limit(limit).all()

    result = []
    for m in matches:
        is_home = (m.home_team_id == team_id)
        own_score = m.home_score if is_home else m.away_score
        opp_score = m.away_score if is_home else m.home_score
        opponent = m.away_team if is_home else m.home_team
        if own_score > opp_score:
            res = "W"
        elif own_score < opp_score:
            res = "L"
        else:
            res = "D"
        result.append({
            "result": res,
            "score": f"{own_score}:{opp_score}",
            "opponent": opponent,
            "home": is_home,
            "date": m.kickoff,
        })
    return result


def get_h2h(home_team_id, away_team_id, limit=5):
    """Letzte direkte Duelle zwischen zwei Teams (beide Heim-Konstellationen)."""
    matches = Match.query.filter(
        Match.status == "finished",
        Match.home_score.isnot(None),
        (
            ((Match.home_team_id == home_team_id) & (Match.away_team_id == away_team_id)) |
            ((Match.home_team_id == away_team_id) & (Match.away_team_id == home_team_id))
        )
    ).order_by(Match.kickoff.desc()).limit(limit).all()

    summary = {"home_wins": 0, "draws": 0, "away_wins": 0, "matches": []}
    for m in matches:
        # Vereinheitlichen: Perspektive home_team_id
        if m.home_team_id == home_team_id:
            hs, as_ = m.home_score, m.away_score
        else:
            hs, as_ = m.away_score, m.home_score
        if hs > as_:
            summary["home_wins"] += 1
        elif hs < as_:
            summary["away_wins"] += 1
        else:
            summary["draws"] += 1
        summary["matches"].append({
            "match": m,
            "score_for_home": f"{hs}:{as_}",
        })
    return summary


# ===================================================== Sondertipps Punkte -
def _normalize(value):
    """Normalisiert einen Antwort-String: trim, lowercase, kollabiert Whitespace."""
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _parse_list(value):
    """Versucht einen Wert als JSON-Liste zu parsen, fallback Komma-Split."""
    if not value:
        return []
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    s = str(value).strip()
    if s.startswith("["):
        try:
            import json as _j
            return [_normalize(v) for v in _j.loads(s)]
        except Exception:
            pass
    return [_normalize(v) for v in s.split(",") if v.strip()]


def compare_special_answer(question, user_answer):
    """Vergleicht User-Antwort mit korrekter Antwort, je nach answer_type.
    Liefert Punkte zurueck (0 oder question.points_value, bei multi_team Teilpunkte).
    """
    if not question.correct_answer:
        return 0
    atype = question.answer_type or "text"

    if atype == "multi_team":
        correct = set(_parse_list(question.correct_answer))
        given = set(_parse_list(user_answer))
        if not correct or not given:
            return 0
        # Teilpunkte: pro korrektem Treffer (max points_value)
        hits = len(correct & given)
        if hits == 0:
            return 0
        # Punkte pro Treffer: points_value / Anzahl korrekter Antworten
        per_hit = question.points_value / max(len(correct), 1)
        return int(round(per_hit * hits))

    if atype == "number":
        try:
            return question.points_value if int(user_answer) == int(question.correct_answer) else 0
        except (ValueError, TypeError):
            return 0

    # text, choice, team, yes_no - Vergleich case-insensitive
    return question.points_value if _normalize(user_answer) == _normalize(question.correct_answer) else 0


def evaluate_special_predictions():
    """Berechnet Punkte fuer alle Sondertipps mit gesetzter correct_answer."""
    questions = SpecialQuestion.query.filter(SpecialQuestion.correct_answer.isnot(None)).all()
    for q in questions:
        if not q.correct_answer:
            continue
        for sp in SpecialPrediction.query.filter_by(question_id=q.id).all():
            sp.points = compare_special_answer(q, sp.answer)
    db.session.commit()


# ============================================================ Ewige Tabelle -
def get_eternal_table():
    """Aggregiert SeasonArchive ueber alle Saisons + aktuelle Saison."""
    archives = SeasonArchive.query.all()
    table = {}
    for a in archives:
        uid = a.user_id
        if uid not in table:
            table[uid] = {
                "user": a.user, "seasons": 0, "points": 0,
                "exact": 0, "diff": 0, "tendency": 0, "wrong": 0,
                "best_rank": 999, "titles": 0,
            }
        t = table[uid]
        t["seasons"] += 1
        t["points"] += a.points
        t["exact"] += a.exact_count
        t["diff"] += a.diff_count
        t["tendency"] += a.tendency_count
        t["wrong"] += a.wrong_count
        if a.rank < t["best_rank"]:
            t["best_rank"] = a.rank
        if a.rank == 1:
            t["titles"] += 1

    # Plus aktuelle Saison (live aus get_user_stats)
    current_season = get_setting("current_season", "2025/26")
    for stats in get_leaderboard():
        uid = stats["user"].id
        if uid not in table:
            table[uid] = {
                "user": stats["user"], "seasons": 1, "points": stats["points"],
                "exact": stats["exact"], "diff": stats["diff"],
                "tendency": stats["tendency"], "wrong": stats["wrong"],
                "best_rank": stats["rank"], "titles": 0,
                "current_season": True,
            }
        else:
            # Wir summieren die LIVE-Saison NICHT doppelt - nur, wenn noch nicht archiviert
            existing_seasons = {a.season for a in archives if a.user_id == uid}
            if current_season not in existing_seasons:
                t = table[uid]
                t["seasons"] += 1
                t["points"] += stats["points"]
                t["exact"] += stats["exact"]
                t["diff"] += stats["diff"]
                t["tendency"] += stats["tendency"]
                t["wrong"] += stats["wrong"]
                if stats["rank"] < t["best_rank"]:
                    t["best_rank"] = stats["rank"]

    rows = sorted(table.values(), key=lambda r: (-r["points"], -r["titles"], r["user"].username))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def archive_season(season_label):
    """Speichert die aktuelle Tabelle als Saison-Archiv."""
    rows = get_leaderboard()
    for r in rows:
        existing = SeasonArchive.query.filter_by(user_id=r["user"].id, season=season_label).first()
        if existing:
            existing.rank = r["rank"]
            existing.points = r["points"]
            existing.exact_count = r["exact"]
            existing.diff_count = r["diff"]
            existing.tendency_count = r["tendency"]
            existing.wrong_count = r["wrong"]
        else:
            db.session.add(SeasonArchive(
                user_id=r["user"].id, season=season_label,
                rank=r["rank"], points=r["points"],
                exact_count=r["exact"], diff_count=r["diff"],
                tendency_count=r["tendency"], wrong_count=r["wrong"],
            ))
    db.session.commit()

def apply_vapid_settings():
    """Spiegelt gespeicherte VAPID-Keys aus der DB in app.config."""
    try:
        pub = get_setting("vapid_public", "")
        priv = get_setting("vapid_private", "")
        if pub:
            current_app.config["VAPID_PUBLIC_KEY"] = pub
        if priv:
            current_app.config["VAPID_PRIVATE_KEY"] = priv
    except Exception:
        pass
