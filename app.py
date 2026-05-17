"""Wulmstörper Tipprunde – Hauptanwendung (Flask)."""
import os
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, render_template, redirect, url_for, flash, request,
    Blueprint, abort, jsonify, send_file, current_app, send_from_directory,
)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from io import BytesIO
import csv

from config import Config
from extensions import db, login_manager, mail, cache, csrf
from models import (
    User, Team, Match, Prediction, Setting, Comment, Badge, UserBadge,
    SpecialQuestion, SpecialPrediction, SeasonArchive, Prize, MatchdayWinner,
    Competition, CompetitionTeam,
)
from forms import (
    RegisterForm, LoginForm, ProfileForm, PasswordResetRequestForm,
    PasswordResetForm, TipForm, CommentForm, MatchResultForm, SettingsForm,
    SpecialQuestionForm, SpecialAnswerForm, BadgeForm,
    AdminUserForm, PrizeForm,
)
from utils import (
    save_avatar, send_password_reset, send_email, get_setting, set_setting,
    calculate_points, recalculate_all_points, sync_results,
    seed_teams_if_empty, seed_demo_matches, seed_badges, seed_prizes,
    check_and_award_badges, get_leaderboard, get_user_stats,
    classify_prediction, compute_live_standings, get_team_position,
    get_team_form, get_h2h, get_eternal_table, archive_season,
    evaluate_special_predictions, get_current_matchday,
    fetch_live_standings, fetch_live_match_updates,
    award_badge, revoke_badge, compute_pot_summary, apply_mail_settings,
    auto_migrate_schema,
    get_user_trend, get_user_insights, get_match_tip_distribution,
    get_match_weather, get_open_matches_for_user, generate_season_pdf,
)
import json as _json


# =================================================================== APP -
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    cache.init_app(app)
    csrf.init_app(app)
    # Flask-Migrate ist absichtlich raus - wir nutzen utils.auto_migrate_schema()

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    # Blueprints registrieren
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")
    
    # Neue Blueprints
    from live_scoring import live_bp
    app.register_blueprint(live_bp, url_prefix="/live")

    # Neue Features
    from push_routes import register_push_routes
    from pwa_routes import register_pwa_routes
    register_push_routes(app)
    register_pwa_routes(app)

    # Asset-Version für Cache-Busting (ändert sich bei jedem App-Restart)
    import time
    _asset_version = str(int(time.time()))

    @app.context_processor
    def inject_globals():
        from flask import session
        ctx = {"now": datetime.utcnow, "asset_version": _asset_version}
        
        # Competition Switcher Daten
        try:
            ctx["active_competition"] = session.get("competition_code", "BL1")
            ctx["all_competitions"] = Competition.query.filter_by(is_active=True).all()
        except Exception:
            ctx["active_competition"] = "BL1"
            ctx["all_competitions"] = []
            
        # Countdown-Banner: ungetippte Spiele in den nächsten 24h
        if current_user.is_authenticated:
            try:
                open_matches = get_open_matches_for_user(current_user, max_hours=24)
                ctx["open_match_count"] = len(open_matches)
                ctx["next_open_match"] = open_matches[0] if open_matches else None
            except Exception:
                ctx["open_match_count"] = 0
                ctx["next_open_match"] = None
        return ctx

    @app.context_processor
    def inject_vapid_key():
        return {"vapid_public_key": app.config.get("VAPID_PUBLIC_KEY", "")}

    @app.template_filter("format_number")
    def format_number_filter(value):
        try:
            return f"{int(value):,}".replace(",", ".")
        except (ValueError, TypeError):
            return value

    DE_WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    DE_WEEKDAYS_LONG  = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    DE_MONTHS_SHORT   = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    DE_MONTHS_LONG    = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                         "Juli", "August", "September", "Oktober", "November", "Dezember"]

    def _german_strftime(value, fmt):
        """strftime-Ersatz mit DEUTSCHEN Wochentagen/Monaten - System-Locale-unabhängig."""
        if not value:
            return "—"
        wd = value.weekday()  # 0=Mo .. 6=So
        m = value.month - 1
        # Eigene Tokens vorab ersetzen
        result = fmt
        result = result.replace("%a", DE_WEEKDAYS_SHORT[wd])
        result = result.replace("%A", DE_WEEKDAYS_LONG[wd])
        result = result.replace("%b", DE_MONTHS_SHORT[m])
        result = result.replace("%B", DE_MONTHS_LONG[m])
        # Restliche Tokens (Tag, Monat, Jahr, Stunde, Minute) per strftime
        return value.strftime(result)

    @app.template_filter("dt")
    def fmt_dt(value, fmt="%d.%m.%Y %H:%M"):
        return _german_strftime(value, fmt)

    @app.template_filter("de")
    def fmt_de(value, fmt="%a, %d.%m. %H:%M"):
        """Deutsches Datum mit Wochentag (z.B. 'Sa, 16.12. 18:30')."""
        return _german_strftime(value, fmt)

    @app.template_filter("fromjson")
    def fromjson(value):
        if not value:
            return []
        try:
            parsed = _json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (ValueError, TypeError):
            return []

    # Bootstrap DB + Demo
    with app.app_context():
        db.create_all()
        
        # 🔥 PERFORMANCE: SQLite WAL-Modus aktivieren (parallel lesen/schreiben!)
        try:
            db.execute("PRAGMA journal_mode=WAL;")
            db.execute("PRAGMA synchronous=NORMAL;")
            db.execute("PRAGMA cache_size=-64000;")  # 64MB RAM-Cache
            db.execute("PRAGMA temp_store=MEMORY;")
            app.logger.info("✅ SQLite WAL-Modus aktiviert (Performance-Boost)")
        except Exception:
            pass  # Ignorieren wenn nicht SQLite
        
        # Auto-Migration: ergänzt fehlende Spalten in alten DBs (SQLite)
        # damit nach Updates kein "no such column"-Fehler auftritt
        try:
            auto_migrate_schema()
        except Exception as e:
            app.logger.warning(f"Auto-Migration übersprungen: {e}")

        # Seed default competition (MUSS vor seed_demo_matches geschehen wegen FK)
        try:
            if Competition.query.count() == 0:
                default_comp = Competition(
                    code="BL1",
                    name="Bundesliga",
                    season="2025/26",
                    is_active=True
                )
                db.session.add(default_comp)
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f"Competition seed failed: {e}")

        seed_teams_if_empty()
        seed_demo_matches()
        seed_badges()
        seed_prizes()

        # === Admin sicherstellen ===
        # Sucht zuerst per E-Mail, dann per Username. Verhindert
        # UNIQUE-Constraint-Crash, falls Username/E-Mail aus alten
        # DB-Versionen voneinander abweichen.
        admin_email = app.config["ADMIN_EMAIL"].lower()
        admin_username = app.config["ADMIN_USERNAME"]
        admin_password = app.config["ADMIN_PASSWORD"]
        admin_reset = os.environ.get("ADMIN_RESET", "").lower() in ("1", "true", "yes")

        try:
            # 1. Suche per E-Mail (eindeutig)
            admin = User.query.filter_by(email=admin_email).first()
            if not admin:
                # 2. Fallback: Suche per Username
                admin = User.query.filter_by(username=admin_username).first()

            if not admin:
                # 3. Wirklich kein Admin → anlegen
                # Vor dem Anlegen erneut auf Konflikte pruefen
                if not User.query.filter(
                    (User.email == admin_email) | (User.username == admin_username)
                ).first():
                    admin = User(
                        username=admin_username,
                        email=admin_email,
                        is_admin=True,
                    )
                    admin.set_password(admin_password)
                    db.session.add(admin)
                    db.session.commit()
                    app.logger.info(f"✅ Admin angelegt: {admin_email} / {admin_password}")
            elif admin_reset:
                # Reset (per ENV ADMIN_RESET=1): E-Mail + Passwort + Username synchen
                # Aber Username/E-Mail nur ändern wenn nicht von ANDEREM User belegt
                conflict_email = User.query.filter(
                    User.email == admin_email, User.id != admin.id
                ).first()
                conflict_user = User.query.filter(
                    User.username == admin_username, User.id != admin.id
                ).first()
                if not conflict_email:
                    admin.email = admin_email
                if not conflict_user:
                    admin.username = admin_username
                admin.set_password(admin_password)
                admin.is_admin = True
                db.session.commit()
                app.logger.warning(
                    f"⚠️ Admin RESET ausgeführt: {admin.email} / {admin_password}"
                )
            elif not admin.is_admin:
                # Existierender User ohne Admin-Rechte → upgraden
                admin.is_admin = True
                db.session.commit()
                app.logger.info(f"✅ Admin-Rechte gesetzt für {admin.email}")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Admin-Bootstrap fehlgeschlagen: {e}")
            # App startet trotzdem - User kann sich mit existierendem Konto einloggen
        # Default-Punkteeinstellungen
        if get_setting("points_exact") is None:
            set_setting("points_exact", app.config["POINTS_EXACT"])
            set_setting("points_diff", app.config["POINTS_DIFF"])
            set_setting("points_tendency", app.config["POINTS_TENDENCY"])
        # Default-Pott-Einstellungen
        if get_setting("pot_amount") is None:
            set_setting("pot_amount", 5)
            set_setting("pot_currency", "€")
            set_setting("pot_intro", "Jeder Mitspieler zahlt seinen Einsatz in den Pott. Am Saisonende wird der gesamte Pott an die Gewinner gemäß Verteilungsplan ausgeschüttet.")

    # === Flask CLI: bequeme Helfer ===
    @app.cli.command("reset-admin")
    def cli_reset_admin():
        """Setzt das Admin-Konto auf die Config-/ENV-Werte zurück."""
        with app.app_context():
            admin = User.query.filter_by(username=app.config["ADMIN_USERNAME"]).first()
            if not admin:
                admin = User(
                    username=app.config["ADMIN_USERNAME"],
                    email=app.config["ADMIN_EMAIL"].lower(),
                    is_admin=True,
                )
                db.session.add(admin)
            admin.email = app.config["ADMIN_EMAIL"].lower()
            admin.is_admin = True
            admin.set_password(app.config["ADMIN_PASSWORD"])
            db.session.commit()
            print(f"✅ Admin zurückgesetzt:")
            print(f"   E-Mail:   {admin.email}")
            print(f"   Passwort: {app.config['ADMIN_PASSWORD']}")
            print(f"   Username: {admin.username}")

    @app.cli.command("set-admin-password")
    def cli_set_admin_password():
        """Interaktiv ein neues Admin-Passwort setzen."""
        import getpass
        with app.app_context():
            email = input(f"Admin-E-Mail [{app.config['ADMIN_EMAIL']}]: ").strip() or app.config["ADMIN_EMAIL"]
            new_pw = getpass.getpass("Neues Passwort: ")
            if len(new_pw) < 6:
                print("❌ Passwort muss mind. 6 Zeichen haben.")
                return
            admin = User.query.filter_by(email=email.lower()).first()
            if not admin:
                admin = User(username="admin", email=email.lower(), is_admin=True)
                db.session.add(admin)
            admin.set_password(new_pw)
            admin.is_admin = True
            db.session.commit()
            print(f"✅ Passwort für {email} gesetzt.")

    return app


# ============================================================ MAIN ROUTES -
main_bp = Blueprint("main", __name__)


# ============================================================
# PWA Routes (Service Worker + Manifest + Icons)
# ============================================================
@main_bp.route("/sw.js")
def service_worker():
    """Service Worker MUSS vom Root-Pfad ausgeliefert werden, damit
    er Scope über die ganze App haben kann (sonst nur /static/)."""
    response = send_from_directory(
        os.path.join(current_app.root_path, "static", "js"),
        "sw.js",
        mimetype="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@main_bp.route("/manifest.json")
def manifest():
    """Web-App-Manifest am Root-Pfad."""
    return send_from_directory(
        os.path.join(current_app.root_path, "static"),
        "manifest.json",
        mimetype="application/manifest+json",
    )


@main_bp.route("/icon-<int:size>.png")
def pwa_icon(size):
    """Generiert PWA-Icons dynamisch via Pillow (oder fällt auf SVG zurück)."""
    if size not in (192, 512):
        size = 192
    try:
        from PIL import Image, ImageDraw, ImageFont
        from io import BytesIO

        img = Image.new("RGB", (size, size), color=(20, 184, 166))  # teal
        draw = ImageDraw.Draw(img)

        # Soccer-Ball als Emoji oder Fallback "⚽"
        text = "⚽"
        # Font-Suche: System-Fonts auf Linux/Windows
        font = None
        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "C:/Windows/Fonts/seguiemj.ttf",   # Win10 Color Emoji
            "C:/Windows/Fonts/arial.ttf",
        ]:
            try:
                font = ImageFont.truetype(font_path, size=int(size * 0.6))
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()

        # Text mittig zeichnen
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (size - tw) // 2 - bbox[0]
            y = (size - th) // 2 - bbox[1]
        except Exception:
            x = y = size // 4
        draw.text((x, y), text, fill="white", font=font)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        from flask import Response
        resp = Response(buf.getvalue(), mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    except Exception as e:
        # Fallback: SVG-Icon mit gleicher Größe
        from flask import Response
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f'<rect width="{size}" height="{size}" fill="#14b8a6"/>'
            f'<text x="{size//2}" y="{int(size*0.7)}" font-size="{int(size*0.6)}" '
            f'text-anchor="middle" fill="white">⚽</text></svg>'
        )
        return Response(svg, mimetype="image/svg+xml")


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    # Top-3 Spieler als kleine Vorschau (nur wenn Punkte vorhanden)
    top3 = get_leaderboard()[:3]
    return render_template(
        "landing.html",
        top3=top3,
        current_md=get_current_matchday(),
    )


@main_bp.route("/dashboard")
@login_required
def dashboard():
    upcoming = Match.query.filter(
        Match.kickoff > datetime.now(timezone.utc),
        Match.status == "scheduled"
    ).order_by(Match.kickoff.asc()).all()  # Alle kommenden Spiele anzeigen

    user_points = current_user.total_points()
    leaderboard = get_leaderboard()[:5]
    user_rank = next((r["rank"] for r in get_leaderboard() if r["user"].id == current_user.id), None)

    # Aktueller Spieltag
    current_matchday = get_current_matchday()

    user_badges = UserBadge.query.filter_by(user_id=current_user.id).all()

    # Live-Match-Hinweis
    live_count = Match.query.filter_by(status="live").count()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_matches_count = Match.query.filter(
        Match.kickoff >= today,
        Match.kickoff < today + timedelta(days=1),
    ).count()

    return render_template(
        "dashboard.html",
        upcoming=upcoming,
        user_points=user_points,
        leaderboard=leaderboard,
        user_rank=user_rank,
        current_matchday=current_matchday,
        badges=user_badges,
        live_count=live_count,
        today_matches_count=today_matches_count,
        get_user_prediction=lambda mid: Prediction.query.filter_by(user_id=current_user.id, match_id=mid).first(),
    )


@main_bp.route("/spielplan")
@main_bp.route("/spielplan/<int:matchday>")
@login_required
def schedule(matchday=None):
    if matchday is None:
        # Automatisch zum aktuellen Spieltag (oder dem nächsten ungespielten) springen
        matchday = get_current_matchday()

    matches = Match.query.filter_by(matchday=matchday).order_by(Match.kickoff).all()
    matchdays = sorted(set(m.matchday for m in Match.query.all()))

    # Prediktion-Map
    pred_map = {}
    for p in Prediction.query.filter_by(user_id=current_user.id).all():
        pred_map[p.match_id] = p

    joker_used = current_user.joker_used_for_matchday(matchday)

    # Tabellenpositionen + Form vorberechnen
    standings = compute_live_standings()
    pos_map = {r["team"].id: r for r in standings}
    form_map = {}
    for m in matches:
        if m.home_team_id not in form_map:
            form_map[m.home_team_id] = get_team_form(m.home_team_id, 5)
        if m.away_team_id not in form_map:
            form_map[m.away_team_id] = get_team_form(m.away_team_id, 5)

    return render_template(
        "schedule.html",
        matches=matches,
        current_md=matchday,
        matchdays=matchdays,
        pred_map=pred_map,
        joker_used=joker_used,
        pos_map=pos_map,
        form_map=form_map,
    )


@main_bp.route("/match/<int:match_id>", methods=["GET", "POST"])
@login_required
def match_detail(match_id):
    match = Match.query.get_or_404(match_id)
    pred = Prediction.query.filter_by(user_id=current_user.id, match_id=match_id).first()

    tip_form = TipForm(obj=pred)
    comment_form = CommentForm()

    # Navigation: alle Matches dieses Spieltags
    siblings = Match.query.filter_by(matchday=match.matchday).order_by(Match.kickoff, Match.id).all()
    sibling_ids = [m.id for m in siblings]
    try:
        idx = sibling_ids.index(match_id)
    except ValueError:
        idx = 0
    prev_id = sibling_ids[idx - 1] if idx > 0 else None
    next_id = sibling_ids[idx + 1] if idx < len(sibling_ids) - 1 else None
    nav_position = idx + 1
    nav_total = len(siblings)

    if tip_form.validate_on_submit() and request.form.get("form") == "tip":
        if not match.is_open():
            flash("Tipps sind nicht mehr möglich – Anpfiff erfolgt.", "danger")
            return redirect(url_for("main.match_detail", match_id=match_id))

        # Joker-Auto-Move: alten Joker am gleichen Spieltag automatisch entfernen
        if tip_form.joker.data and not (pred and pred.joker):
            existing = Prediction.query.join(Match).filter(
                Prediction.user_id == current_user.id,
                Prediction.joker.is_(True),
                Match.matchday == match.matchday,
                Prediction.match_id != match_id,
            ).all()
            for ep in existing:
                old_label = f"{ep.match.home_team.short_name}-{ep.match.away_team.short_name}"
                ep.joker = False
                flash(f"⚡ Joker von {old_label} hierher verschoben.", "info")

        if pred:
            pred.home_tip = tip_form.home_tip.data
            pred.away_tip = tip_form.away_tip.data
            pred.joker = tip_form.joker.data
        else:
            pred = Prediction(
                user_id=current_user.id, match_id=match_id,
                home_tip=tip_form.home_tip.data, away_tip=tip_form.away_tip.data,
                joker=tip_form.joker.data,
            )
            db.session.add(pred)
        db.session.commit()
        check_and_award_badges()
        flash("Tipp gespeichert!", "success")
        return redirect(url_for("main.match_detail", match_id=match_id))

    if comment_form.validate_on_submit() and request.form.get("form") == "comment":
        c = Comment(match_id=match_id, user_id=current_user.id, text=comment_form.text.data)
        db.session.add(c)
        db.session.commit()
        flash("Kommentar gepostet.", "success")
        return redirect(url_for("main.match_detail", match_id=match_id))

    comments = Comment.query.filter_by(match_id=match_id).order_by(Comment.created_at.desc()).all()
    all_preds = match.predictions.all() if match.status == "finished" else []

    # Erweiterte Daten: Form, H2H, Tabellenposition
    home_form = get_team_form(match.home_team_id, limit=5)
    away_form = get_team_form(match.away_team_id, limit=5)
    h2h = get_h2h(match.home_team_id, match.away_team_id, limit=5)
    home_pos = get_team_position(match.home_team_id)
    away_pos = get_team_position(match.away_team_id)
    # Neue Insights: Tipp-Verteilung + Wetter
    tip_dist = get_match_tip_distribution(match_id)
    weather = None
    if match.is_open() or match.status == "live":
        try:
            weather = get_match_weather(match)
        except Exception as e:
            current_app.logger.warning(f"Wetter-API Fehler: {e}")

    return render_template(
        "match_detail.html",
        match=match, pred=pred,
        tip_form=tip_form, comment_form=comment_form,
        comments=comments, all_preds=all_preds,
        home_form=home_form, away_form=away_form, h2h=h2h,
        home_pos=home_pos, away_pos=away_pos,
        sibling_ids=sibling_ids, prev_id=prev_id, next_id=next_id,
        nav_position=nav_position, nav_total=nav_total,
        siblings=siblings,
        tip_dist=tip_dist, weather=weather,
    )


@main_bp.route("/schnelltipp/<int:matchday>", methods=["GET", "POST"])
@login_required
def quick_tip(matchday):
    matches = Match.query.filter_by(matchday=matchday).order_by(Match.kickoff).all()

    if request.method == "POST":
        joker_match_id = request.form.get("joker_match", type=int)
        used_joker = False
        for m in matches:
            if not m.is_open():
                continue
            h = request.form.get(f"home_{m.id}", type=int)
            a = request.form.get(f"away_{m.id}", type=int)
            if h is None or a is None:
                continue

            is_joker = (m.id == joker_match_id and not used_joker)
            if is_joker:
                used_joker = True

            pred = Prediction.query.filter_by(user_id=current_user.id, match_id=m.id).first()
            if pred:
                pred.home_tip = h
                pred.away_tip = a
                pred.joker = is_joker
            else:
                db.session.add(Prediction(
                    user_id=current_user.id, match_id=m.id,
                    home_tip=h, away_tip=a, joker=is_joker,
                ))
        db.session.commit()
        check_and_award_badges()
        
        # 🔥 PERFORMANCE: Cache invalidieren nach Tipp-Änderung
        try:
            from cache import invalidate_leaderboard
            invalidate_leaderboard()
        except Exception:
            pass
        
        flash(f"Schnelltipps für Spieltag {matchday} gespeichert.", "success")
        return redirect(url_for("main.schedule", matchday=matchday))

    pred_map = {p.match_id: p for p in Prediction.query.filter_by(user_id=current_user.id).all()}

    # Form & Tabellenpositionen für jedes Team vorbereiten
    standings = compute_live_standings()
    pos_map = {r["team"].id: r for r in standings}
    form_map = {}
    for m in matches:
        if m.home_team_id not in form_map:
            form_map[m.home_team_id] = get_team_form(m.home_team_id, 5)
        if m.away_team_id not in form_map:
            form_map[m.away_team_id] = get_team_form(m.away_team_id, 5)

    return render_template(
        "quick_tip.html",
        matches=matches, matchday=matchday, pred_map=pred_map,
        pos_map=pos_map, form_map=form_map,
    )


# ================================================ Sondertipps (Special) -
@main_bp.route("/sondertipps", methods=["GET", "POST"])
@login_required
def special_tips():
    questions = SpecialQuestion.query.order_by(SpecialQuestion.deadline.asc()).all()

    if request.method == "POST":
        for q in questions:
            if datetime.now(timezone.utc) > q.deadline:
                continue

            # Antwort je nach Typ einsammeln
            if q.answer_type == "multi_team":
                # Mehrere Checkboxen → Liste
                values = request.form.getlist(f"q_{q.id}")
                values = [v.strip() for v in values if v.strip()]
                if not values:
                    continue
                # Bei zu vielen Antworten: kappen auf multi_count
                if q.multi_count and len(values) > q.multi_count:
                    flash(f"Bei '{q.text[:40]}': max. {q.multi_count} Antworten erlaubt. Erste {q.multi_count} gespeichert.", "warning")
                    values = values[:q.multi_count]
                answer = _json.dumps(values)
            else:
                answer = request.form.get(f"q_{q.id}", "").strip()
                if not answer:
                    continue

            sp = SpecialPrediction.query.filter_by(user_id=current_user.id, question_id=q.id).first()
            if sp:
                sp.answer = answer
            else:
                db.session.add(SpecialPrediction(
                    user_id=current_user.id, question_id=q.id, answer=answer,
                ))
        db.session.commit()
        # Punkte ggf. aktualisieren wenn Auflösung schon da
        from utils import evaluate_special_predictions as _eval
        _eval()
        flash("Sondertipps gespeichert.", "success")
        return redirect(url_for("main.special_tips"))

    user_answers = {sp.question_id: sp for sp in SpecialPrediction.query.filter_by(user_id=current_user.id).all()}

    # Bei multi_team: User-Antwort als Liste parsen für Template
    user_answer_lists = {}
    for qid, sp in user_answers.items():
        try:
            parsed = _json.loads(sp.answer)
            if isinstance(parsed, list):
                user_answer_lists[qid] = parsed
        except (ValueError, TypeError):
            pass

    # Optionen parsen je Frage
    parsed_options = {}
    for q in questions:
        if q.answer_type == "choice" and q.options:
            try:
                parsed_options[q.id] = _json.loads(q.options)
            except (ValueError, TypeError):
                parsed_options[q.id] = [o.strip() for o in q.options.split("\n") if o.strip()]
        else:
            parsed_options[q.id] = None

    # Alle Teams für team/multi_team Fragen
    all_teams = Team.query.order_by(Team.name).all()

    return render_template(
        "special_tips.html",
        questions=questions, user_answers=user_answers,
        user_answer_lists=user_answer_lists,
        options=parsed_options, all_teams=all_teams,
        current_time=datetime.now(timezone.utc),
    )


# ================================================ Ewige Tabelle -
@main_bp.route("/ewige-tabelle")
@login_required
def eternal_table():
    rows = get_eternal_table()
    return render_template("eternal.html", rows=rows)


# ================================================ Saison-Recap -
@main_bp.route("/recap")
@login_required
def season_recap():
    """Persönlicher Saison-Wrapped-Stil mit allen Highlights."""
    insights = get_user_insights(current_user)
    stats = _compute_profile_stats(current_user)
    trend = get_user_trend(current_user.id, last_n_matchdays=34)
    md_wins = MatchdayWinner.query.filter_by(user_id=current_user.id).all()
    badges = UserBadge.query.filter_by(user_id=current_user.id).all()

    # Bestes Spieltag-Punkte
    from sqlalchemy import func
    best_md_q = db.session.query(
        Match.matchday,
        func.coalesce(func.sum(Prediction.points), 0).label("pts"),
    ).join(Prediction, Prediction.match_id == Match.id) \
     .filter(Prediction.user_id == current_user.id, Match.status == "finished") \
     .group_by(Match.matchday).order_by(func.sum(Prediction.points).desc()).first()
    best_md_pts = best_md_q.pts if best_md_q else 0
    best_md_num = best_md_q.matchday if best_md_q else None

    # Joker-Statistik
    joker_preds = [p for p in current_user.predictions.all() if p.joker]
    joker_total_pts = sum(p.points or 0 for p in joker_preds)
    joker_avg = round(joker_total_pts / len(joker_preds), 1) if joker_preds else 0

    # Bester / aktueller Rang (vereinfacht)
    final_rank = trend.get("current_rank")
    best_rank = final_rank
    if trend.get("previous_rank") and trend.get("current_rank"):
        best_rank = min(trend["previous_rank"], trend["current_rank"])

    return render_template(
        "recap.html",
        insights=insights, stats=stats, trend=trend,
        md_wins_count=len(md_wins), badges_count=len(badges),
        best_md_pts=best_md_pts, best_md_num=best_md_num,
        joker_total_pts=joker_total_pts, joker_avg=joker_avg,
        joker_count=len(joker_preds),
        final_rank=final_rank, best_rank=best_rank,
    )


# ================================================ Preise & Pott -
@main_bp.route("/preise")
@login_required
def prizes():
    """Öffentliche Gewinner-Seite mit Preisen und Pott-Übersicht."""
    all_prizes = Prize.query.filter_by(active=True).order_by(
        Prize.sort_order.asc(), Prize.rank.asc()
    ).all()
    pot = compute_pot_summary()
    # Aktuelle Tabelle für die Vorschau, wer aktuell wo steht
    leaderboard = get_leaderboard()[:10]
    return render_template(
        "prizes.html", prizes=all_prizes, pot=pot,
        leaderboard=leaderboard,
    )


# =================================================== Live Match Center -
@main_bp.route("/live")
@login_required
def live_center():
    """Live-Center: Spiele links, Tipp-Tabelle rechts mit Rang-Animationen."""
    now = datetime.now(timezone.utc)
    # Spiele in einem +/- 4h-Fenster anzeigen (heute)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Live + heute geplante + heute beendete
    live_matches = Match.query.filter(
        Match.kickoff >= today_start,
        Match.kickoff < today_end,
    ).order_by(Match.kickoff.asc()).all()

    # Falls heute keine Spiele: nimm den nächsten Tag mit Spielen
    if not live_matches:
        next_match = Match.query.filter(Match.kickoff >= now).order_by(Match.kickoff.asc()).first()
        if next_match:
            day = next_match.kickoff.replace(hour=0, minute=0, second=0, microsecond=0)
            live_matches = Match.query.filter(
                Match.kickoff >= day,
                Match.kickoff < day + timedelta(days=1),
            ).order_by(Match.kickoff.asc()).all()

    # Aktuelle Tipp-Tabelle
    leaderboard = get_leaderboard()

    # User-Tipps zu allen angezeigten Matches (für anpassbare Score-Diff-Anzeige)
    user_preds = {
        p.match_id: p
        for p in Prediction.query.filter_by(user_id=current_user.id).all()
        if p.match_id in [m.id for m in live_matches]
    }

    return render_template(
        "live.html",
        matches=live_matches,
        leaderboard=leaderboard,
        user_preds=user_preds,
        is_today=bool(live_matches and live_matches[0].kickoff.date() == now.date()),
    )


# ================================================ Live Bundesliga-Tabelle -
@main_bp.route("/bundesliga-tabelle")
@login_required
def bl_standings():
    """Versucht zuerst die Live-Tabelle von football-data.org zu holen,
    fällt zurück auf lokale Berechnung aus den eingetragenen Spielen."""
    source = "lokal"
    error_msg = None
    standings, err = fetch_live_standings()
    if standings:
        source = "football-data.org (live)"
    else:
        error_msg = err
        standings = compute_live_standings()
    return render_template(
        "standings.html",
        standings=standings,
        source=source,
        error_msg=error_msg,
    )


@main_bp.route("/tabelle")
@main_bp.route("/tabelle/<int:matchday>")
@login_required
def leaderboard(matchday=None):
    rows = get_leaderboard(matchday=matchday)
    matchdays = sorted(set(m.matchday for m in Match.query.all()))
    # Anzahl Spieltagsiege pro User für Badge-Anzeige
    from sqlalchemy import func
    md_wins = dict(
        db.session.query(MatchdayWinner.user_id, func.count(MatchdayWinner.id))
        .group_by(MatchdayWinner.user_id).all()
    )
    # Trend pro User (Sparkline + Rang-Delta) - nur Gesamttabelle
    trends = {}
    if not matchday:
        for r in rows:
            try:
                trends[r["user"].id] = get_user_trend(r["user"].id, last_n_matchdays=6)
            except Exception:
                pass
    return render_template(
        "leaderboard.html",
        rows=rows, current_md=matchday, matchdays=matchdays,
        md_wins=md_wins, trends=trends,
    )


@main_bp.route("/spieltagsieger")
@login_required
def matchday_winners():
    """Übersicht aller Spieltagsieger der laufenden Saison."""
    winners = MatchdayWinner.query.order_by(MatchdayWinner.matchday.desc()).all()
    # Nach Spieltag gruppieren (mehrere Sieger bei geteiltem Sieg)
    from collections import OrderedDict
    grouped = OrderedDict()
    for w in winners:
        grouped.setdefault(w.matchday, []).append(w)

    # Bestenliste: wer hat am meisten Siege?
    from sqlalchemy import func
    top_winners_q = (
        db.session.query(
            MatchdayWinner.user_id,
            func.count(MatchdayWinner.id).label("wins"),
            func.sum(MatchdayWinner.points).label("total_pts"),
        )
        .group_by(MatchdayWinner.user_id)
        .order_by(func.count(MatchdayWinner.id).desc())
        .all()
    )
    top_winners = []
    for row in top_winners_q:
        u = db.session.get(User, row.user_id)
        if u:
            top_winners.append({"user": u, "wins": row.wins, "total_pts": row.total_pts or 0})

    return render_template(
        "matchday_winners.html",
        grouped=grouped, top_winners=top_winners,
    )


def _compute_profile_stats(user):
    """Aggregierte Stats fuer Profil-Anzeige."""
    user_preds = user.predictions.all()
    md_wins = MatchdayWinner.query.filter_by(user_id=user.id).count()
    return {
        "total_tips": len(user_preds),
        "total_points": sum(p.points or 0 for p in user_preds),
        "exact": sum(1 for p in user_preds if p.points and p.points >= get_setting("points_exact", 4)),
        "joker_used": sum(1 for p in user_preds if p.joker),
        "md_wins": md_wins,
    }


def _compute_form_curve(user):
    """Punkte pro Spieltag fuer Chart.js."""
    md_points = {}
    for p in user.predictions.all():
        if p.match and p.match.status == "finished":
            md_points.setdefault(p.match.matchday, 0)
            md_points[p.match.matchday] += p.points or 0
    return sorted(md_points.items())


@main_bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    # Lieblingsverein-Optionen befüllen
    teams = Team.query.order_by(Team.name).all()
    form.favorite_team_id.choices = [(0, "— kein Lieblingsverein —")] + [
        (t.id, t.name) for t in teams
    ]
    if request.method == "GET":
        form.favorite_team_id.data = current_user.favorite_team_id or 0

    if form.validate_on_submit():
        # === Username aktualisieren ===
        new_username = (form.username.data or "").strip()
        if new_username and new_username != current_user.username:
            existing = User.query.filter(
                User.username == new_username,
                User.id != current_user.id,
            ).first()
            if existing:
                flash(f"Spielername '{new_username}' ist bereits vergeben.", "danger")
            else:
                current_user.username = new_username

        # Voller Name
        current_user.full_name = (form.full_name.data or "").strip() or None

        # Sichtbarkeits-Flag fürs öffentliche Anzeigen
        current_user.show_full_name = bool(form.show_full_name.data)

        # Telefonnummer
        current_user.phone = (form.phone.data or "").strip() or None

        # WhatsApp (CallMeBot)
        current_user.whatsapp_phone = (form.whatsapp_phone.data or "").strip() or None
        current_user.whatsapp_apikey = (form.whatsapp_apikey.data or "").strip() or None

        # Lieblingsverein (0 = keiner)
        current_user.favorite_team_id = form.favorite_team_id.data or None

        # === Avatar aktualisieren (komplett defensiv) ===
        try:
            avatar_filename, avatar_error = save_avatar(form.avatar.data, current_user.id)
            if avatar_error:
                flash(f"Avatar nicht übernommen: {avatar_error}", "warning")
            elif avatar_filename:
                current_user.avatar = avatar_filename
        except Exception as e:
            current_app.logger.exception("Unerwarteter Fehler beim Avatar-Upload")
            flash(f"Avatar-Upload fehlgeschlagen: {e}", "danger")

        try:
            db.session.commit()
            flash("Profil gespeichert.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("DB-Fehler beim Profil speichern")
            flash(f"Fehler beim Speichern: {e}", "danger")
        return redirect(url_for("main.profile"))

    # Falls Validierung fehlgeschlagen ist - Fehler anzeigen
    if request.method == "POST":
        for field, errors in form.errors.items():
            for err in errors:
                flash(f"{field}: {err}", "danger")

    stats = _compute_profile_stats(current_user)
    form_curve = _compute_form_curve(current_user)
    badges = UserBadge.query.filter_by(user_id=current_user.id).all()
    pot = compute_pot_summary()
    insights = get_user_insights(current_user)
    trend = get_user_trend(current_user.id)
    return render_template(
        "profile.html", form=form, stats=stats, badges=badges,
        form_curve=form_curve, pot=pot,
        insights=insights, trend=trend,
    )


@main_bp.route("/h2h/<int:user_id>")
@login_required
def head_to_head(user_id):
    other = User.query.get_or_404(user_id)
    my_preds = {p.match_id: p for p in current_user.predictions.all()}
    other_preds = {p.match_id: p for p in other.predictions.all()}
    common_matches = Match.query.filter(Match.id.in_(set(my_preds) & set(other_preds))).all()

    me_pts = sum(my_preds[m.id].points or 0 for m in common_matches)
    other_pts = sum(other_preds[m.id].points or 0 for m in common_matches)

    return render_template(
        "h2h.html", other=other, common=common_matches,
        my=my_preds, their=other_preds, me_pts=me_pts, other_pts=other_pts,
    )


@main_bp.route("/export/pdf")
@login_required
def export_pdf():
    """Generiert einen schönen PDF-Saison-Report für den eingeloggten User."""
    pdf_buf = generate_season_pdf(current_user)
    if pdf_buf is None:
        flash("PDF-Export benötigt das Paket 'reportlab' (pip install reportlab).", "danger")
        return redirect(url_for("main.season_recap"))
    filename = f"Saison-Report_{current_user.username}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf",
                      as_attachment=True, download_name=filename)


@main_bp.route("/export/csv")
@login_required
def export_csv():
    output = BytesIO()
    out_str = []
    writer_data = [["Spieltag", "Datum", "Heim", "Auswärts", "Tipp", "Ergebnis", "Punkte", "Joker"]]
    for p in current_user.predictions.all():
        m = p.match
        writer_data.append([
            m.matchday, m.kickoff.strftime("%d.%m.%Y %H:%M"),
            m.home_team.name, m.away_team.name,
            f"{p.home_tip}:{p.away_tip}",
            f"{m.home_score}:{m.away_score}" if m.home_score is not None else "—",
            p.points or 0, "Ja" if p.joker else "Nein",
        ])
    csv_data = "\n".join(";".join(str(c) for c in row) for row in writer_data)
    output.write(csv_data.encode("utf-8-sig"))
    output.seek(0)
    return send_file(
        output, mimetype="text/csv",
        as_attachment=True, download_name=f"tipps_{current_user.username}.csv"
    )





@main_bp.route("/set-competition/<string:code>")
@login_required
def set_competition(code):
    from flask import session
    comp = Competition.query.filter_by(code=code, is_active=True).first()
    if comp:
        session["competition_code"] = code
        flash(f"Wettbewerb auf '{comp.name}' gewechselt.", "success")
    else:
        flash("Ungültiger Wettbewerb.", "danger")
    return redirect(request.referrer or url_for("main.dashboard"))


# ============================================================== AUTH -
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = RegisterForm()
    if form.validate_on_submit():
        u = User(username=form.username.data, email=form.email.data.lower())
        u.set_password(form.password.data)
        db.session.add(u)
        db.session.commit()
        login_user(u)
        flash("Willkommen beim Tippspiel!", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data.lower()).first()
        if u and u.check_password(form.password.data):
            login_user(u, remember=form.remember.data)
            return redirect(request.args.get("next") or url_for("main.dashboard"))
        flash("E-Mail oder Passwort falsch.", "danger")
    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Abgemeldet.", "info")
    return redirect(url_for("main.index"))


@auth_bp.route("/passwort-vergessen", methods=["GET", "POST"])
def password_reset_request():
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data.lower()).first()
        if u:
            send_password_reset(u)
        flash("Falls die E-Mail existiert, wurde ein Link gesendet.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/password_reset_request.html", form=form)


@auth_bp.route("/passwort-zuruecksetzen/<token>", methods=["GET", "POST"])
def password_reset(token):
    from utils import verify_reset_token
    user = verify_reset_token(token)
    if not user:
        flash("Reset-Link ungültig oder abgelaufen.", "danger")
        return redirect(url_for("auth.login"))
    form = PasswordResetForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash("Passwort geändert. Du kannst dich jetzt anmelden.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/password_reset.html", form=form)


# ============================================================ ADMIN -
admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*a, **kw)
    return wrapper


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    stats = {
        "users": User.query.count(),
        "matches": Match.query.count(),
        "predictions": Prediction.query.count(),
        "finished": Match.query.filter_by(status="finished").count(),
    }
    pot = compute_pot_summary()
    return render_template("admin/dashboard.html", stats=stats, pot=pot)


@admin_bp.route("/sync")
@login_required
@admin_required
def sync():
    res = sync_results()
    flash(res["msg"], "success" if res["ok"] else "danger")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/purge-demo", methods=["POST"])
@login_required
@admin_required
def purge_demo():
    """Loescht alle Demo-Matches (ohne external_id), z.B. nach erfolgreichem
    API-Sync, falls vorher nur Demo-Daten da waren und jetzt parallel laufen."""
    from utils import _purge_demo_matches
    count = _purge_demo_matches()
    flash(f"{count} Demo-Spiele entfernt.", "success" if count else "info")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/purge-all-matches", methods=["POST"])
@login_required
@admin_required
def purge_all_matches():
    """Notfall: ALLE Matches loeschen (inkl. Tipps)."""
    from models import Comment
    Prediction.query.delete()
    Comment.query.delete()
    Match.query.delete()
    db.session.commit()
    flash("⚠️ Alle Matches und Tipps gelöscht. Klicke 'Sync', um neu zu laden.", "warning")
    return redirect(url_for("admin.dashboard"))


# ============================================================ Backup / Restore -
def _sqlite_db_path():
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        return None
    raw = uri.replace("sqlite:///", "", 1)
    if os.path.isabs(raw):
        return raw
    return os.path.join(current_app.root_path, raw)


@admin_bp.route("/backup", methods=["GET"])
@login_required
@admin_required
def backup_page():
    db_path = _sqlite_db_path()
    exists = bool(db_path and os.path.exists(db_path))
    size = os.path.getsize(db_path) if exists else 0
    return render_template("admin/backup.html", db_path=db_path, exists=exists, size=size)


@admin_bp.route("/backup/download")
@login_required
@admin_required
def backup_download():
    """SQLite-Datenbank als .db-Datei herunterladen."""
    db_path = _sqlite_db_path()
    if not db_path or not os.path.exists(db_path):
        flash("Backup nicht möglich: SQLite-Datenbank nicht gefunden.", "danger")
        return redirect(url_for("admin.backup_page"))

    # Saubere Kopie via sqlite backup API erstellen
    import sqlite3
    from io import BytesIO
    src = sqlite3.connect(db_path)
    tmp = sqlite3.connect(":memory:")
    try:
        src.backup(tmp)
        dump_path = db_path  # Fallback: send file directly if memory copy not serializable
    finally:
        src.close()
        tmp.close()

    filename = f"wulmstoerper_tipprunde_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
    return send_file(db_path, as_attachment=True, download_name=filename,
                     mimetype="application/octet-stream")


@admin_bp.route("/backup/restore", methods=["POST"])
@login_required
@admin_required
def backup_restore():
    """SQLite-Datenbank aus hochgeladener .db-Datei wiederherstellen."""
    db_path = _sqlite_db_path()
    if not db_path:
        flash("Restore ist nur bei SQLite-Datenbanken verfügbar.", "danger")
        return redirect(url_for("admin.backup_page"))

    uploaded = request.files.get("backup_file")
    if not uploaded or not uploaded.filename:
        flash("Bitte eine .db-Datei auswählen.", "danger")
        return redirect(url_for("admin.backup_page"))

    if not uploaded.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        flash("Ungültiger Dateityp. Bitte .db/.sqlite hochladen.", "danger")
        return redirect(url_for("admin.backup_page"))

    import sqlite3
    import tempfile
    import shutil

    # Upload zuerst temporär speichern und grob validieren
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)
    try:
        uploaded.save(tmp_path)
        try:
            con = sqlite3.connect(tmp_path)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            valid = cur.fetchone() is not None
            con.close()
        except sqlite3.DatabaseError:
            valid = False
        if not valid:
            flash("Diese Datei sieht nicht wie eine gültige Tipprunden-Datenbank aus (users-Tabelle fehlt oder Datei ist beschädigt).", "danger")
            return redirect(url_for("admin.backup_page"))

        # Aktuelle DB sichern
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        if os.path.exists(db_path):
            backup_before = db_path + ".before_restore_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            shutil.copy2(db_path, backup_before)

        # SQLAlchemy-Verbindungen schließen, Datei ersetzen
        db.session.remove()
        db.engine.dispose()
        shutil.copy2(tmp_path, db_path)

        flash("✅ Backup wurde wiederhergestellt. Bitte die App einmal neu starten bzw. im Browser neu laden.", "success")
        return redirect(url_for("admin.dashboard"))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@admin_bp.route("/matches")
@login_required
@admin_required
def matches():
    md = request.args.get("matchday", 1, type=int)
    matches = Match.query.filter_by(matchday=md).order_by(Match.kickoff).all()
    matchdays = sorted(set(m.matchday for m in Match.query.all()))
    return render_template("admin/matches.html", matches=matches, current_md=md, matchdays=matchdays)


@admin_bp.route("/match/<int:match_id>/result", methods=["GET", "POST"])
@login_required
@admin_required
def edit_result(match_id):
    match = Match.query.get_or_404(match_id)
    form = MatchResultForm(obj=match)
    if form.validate_on_submit():
        match.home_score = form.home_score.data
        match.away_score = form.away_score.data
        match.status = "finished"
        db.session.commit()
        recalculate_all_points()
        check_and_award_badges()
        flash(f"Ergebnis {match.home_team.short_name} {match.home_score}:{match.away_score} {match.away_team.short_name} gespeichert.", "success")
        return redirect(url_for("admin.matches", matchday=match.matchday))
    return render_template("admin/edit_result.html", match=match, form=form)


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    pot = compute_pot_summary()
    return render_template("admin/users.html", users=all_users, pot=pot)


@admin_bp.route("/user/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def user_edit(user_id):
    u = User.query.get_or_404(user_id)
    form = AdminUserForm(obj=u)
    teams = Team.query.order_by(Team.name).all()
    form.favorite_team_id.choices = [(0, "— kein Lieblingsverein —")] + [
        (t.id, t.name) for t in teams
    ]
    if request.method == "GET":
        form.favorite_team_id.data = u.favorite_team_id or 0

    if form.validate_on_submit():
        # Username-Eindeutigkeit
        new_uname = (form.username.data or "").strip()
        if new_uname != u.username:
            other = User.query.filter(User.username == new_uname, User.id != u.id).first()
            if other:
                flash(f"Spielername '{new_uname}' ist bereits vergeben.", "danger")
                return render_template("admin/user_edit.html", user=u, form=form)
        # E-Mail-Eindeutigkeit
        new_email = (form.email.data or "").strip().lower()
        if new_email != u.email:
            other = User.query.filter(User.email == new_email, User.id != u.id).first()
            if other:
                flash(f"E-Mail '{new_email}' ist bereits vergeben.", "danger")
                return render_template("admin/user_edit.html", user=u, form=form)

        u.username = new_uname
        u.full_name = (form.full_name.data or "").strip() or None
        u.show_full_name = bool(form.show_full_name.data)
        u.email = new_email
        u.phone = (form.phone.data or "").strip() or None
        u.favorite_team_id = form.favorite_team_id.data or None
        # Self-Lock: man darf sich nicht selbst die Admin-Rechte entziehen
        if u.id == current_user.id and not form.is_admin.data:
            flash("Du kannst dir die Admin-Rechte nicht selbst entziehen.", "warning")
        else:
            u.is_admin = form.is_admin.data

        # Bezahl-Status
        was_paid = u.has_paid
        u.has_paid = form.has_paid.data
        u.paid_note = (form.paid_note.data or "").strip() or None
        if u.has_paid and not was_paid:
            u.paid_at = datetime.now(timezone.utc)
        elif not u.has_paid:
            u.paid_at = None

        # Optional: neues Passwort setzen
        if form.new_password.data:
            u.set_password(form.new_password.data)

        db.session.commit()
        flash(f"Spieler '{u.username}' aktualisiert.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_edit.html", user=u, form=form)


@admin_bp.route("/user/<int:user_id>/toggle_paid", methods=["POST"])
@login_required
@admin_required
def toggle_paid(user_id):
    """Schnell-Toggle für Bezahl-Status (ein Klick aus der User-Liste)."""
    u = User.query.get_or_404(user_id)
    u.has_paid = not u.has_paid
    if u.has_paid:
        u.paid_at = datetime.now(timezone.utc)
    else:
        u.paid_at = None
    db.session.commit()
    flash(
        f"{u.username}: {'✅ Als bezahlt markiert' if u.has_paid else '❌ Bezahlung entfernt'}.",
        "success",
    )
    return redirect(url_for("admin.users"))


@admin_bp.route("/user/<int:user_id>/toggle_admin", methods=["POST"])
@login_required
@admin_required
def toggle_admin(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash("Du kannst dich nicht selbst entfernen.", "warning")
    else:
        u.is_admin = not u.is_admin
        db.session.commit()
        flash(f"{u.username} ist jetzt {'Admin' if u.is_admin else 'normaler User'}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/user/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash("Du kannst dich nicht selbst löschen.", "warning")
    else:
        db.session.delete(u)
        db.session.commit()
        flash(f"User {u.username} gelöscht.", "info")
    return redirect(url_for("admin.users"))


# ============================================================ Prizes -
@admin_bp.route("/prizes")
@login_required
@admin_required
def admin_prizes():
    """Admin-Übersicht aller Preise + Pott-Status."""
    all_prizes = Prize.query.order_by(Prize.sort_order.asc(), Prize.rank.asc()).all()
    pot = compute_pot_summary()
    return render_template("admin/prizes.html", prizes=all_prizes, pot=pot)


@admin_bp.route("/prizes/new", methods=["GET", "POST"])
@admin_bp.route("/prizes/<int:prize_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def prize_form(prize_id=None):
    prize = db.session.get(Prize, prize_id) if prize_id else None
    form = PrizeForm(obj=prize)
    if form.validate_on_submit():
        if not prize:
            prize = Prize()
            db.session.add(prize)
        prize.rank = form.rank.data
        prize.title = form.title.data.strip()
        prize.description = (form.description.data or "").strip() or None
        prize.icon = form.icon.data.strip() or "🏆"
        prize.color = form.color.data.strip() or "#fbbf24"
        prize.amount = (form.amount.data or "").strip() or None
        prize.detail = (form.detail.data or "").strip() or None
        prize.active = form.active.data
        prize.sort_order = form.sort_order.data or 0
        db.session.commit()
        flash(f"Preis '{prize.title}' gespeichert.", "success")
        return redirect(url_for("admin.admin_prizes"))
    return render_template("admin/prize_form.html", form=form, prize=prize)


@admin_bp.route("/prizes/<int:prize_id>/delete", methods=["POST"])
@login_required
@admin_required
def prize_delete(prize_id):
    prize = Prize.query.get_or_404(prize_id)
    title = prize.title
    db.session.delete(prize)
    db.session.commit()
    flash(f"Preis '{title}' gelöscht.", "info")
    return redirect(url_for("admin.admin_prizes"))


# ============================================================ Badges -
@admin_bp.route("/badges")
@login_required
@admin_required
def badges():
    """Übersicht aller Badges + Statistik."""
    all_badges = Badge.query.order_by(Badge.created_at.desc()).all()
    # Anzahl Vergaben pro Badge
    from sqlalchemy import func
    award_counts = dict(
        db.session.query(UserBadge.badge_id, func.count(UserBadge.id))
        .group_by(UserBadge.badge_id).all()
    )
    return render_template(
        "admin/badges.html",
        badges=all_badges,
        award_counts=award_counts,
    )


@admin_bp.route("/badges/new", methods=["GET", "POST"])
@admin_bp.route("/badges/<int:badge_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def badge_form(badge_id=None):
    badge = db.session.get(Badge, badge_id) if badge_id else None
    form = BadgeForm(obj=badge)
    if form.validate_on_submit():
        # Code-Eindeutigkeit pruefen
        existing = Badge.query.filter(
            Badge.code == form.code.data,
            Badge.id != (badge.id if badge else 0),
        ).first()
        if existing:
            flash(f"Badge-Code '{form.code.data}' ist bereits vergeben.", "danger")
            return render_template("admin/badge_form.html", form=form, badge=badge)

        if not badge:
            badge = Badge(code=form.code.data)
            db.session.add(badge)
        badge.code = form.code.data.strip()
        badge.name = form.name.data.strip()
        badge.description = form.description.data.strip()
        badge.icon = form.icon.data.strip() or "🏅"
        badge.color = form.color.data.strip() or "#fbbf24"
        badge.trigger_type = form.trigger_type.data
        badge.threshold = form.threshold.data or 0
        badge.active = form.active.data
        db.session.commit()

        # Bei Auto-Trigger: sofort prüfen, wer das Badge jetzt verdient
        if badge.trigger_type != "manual" and badge.active:
            check_and_award_badges()

        flash(f"Badge '{badge.name}' gespeichert.", "success")
        return redirect(url_for("admin.badges"))

    return render_template("admin/badge_form.html", form=form, badge=badge)


@admin_bp.route("/badges/<int:badge_id>/delete", methods=["POST"])
@login_required
@admin_required
def badge_delete(badge_id):
    badge = Badge.query.get_or_404(badge_id)
    name = badge.name
    UserBadge.query.filter_by(badge_id=badge.id).delete()
    db.session.delete(badge)
    db.session.commit()
    flash(f"Badge '{name}' gelöscht.", "info")
    return redirect(url_for("admin.badges"))


@admin_bp.route("/badges/<int:badge_id>/award", methods=["GET", "POST"])
@login_required
@admin_required
def badge_award(badge_id):
    """Manuelle Vergabe an einzelne User."""
    badge = Badge.query.get_or_404(badge_id)
    if request.method == "POST":
        action = request.form.get("action", "award")
        user_ids = request.form.getlist("user_ids", type=int)
        count = 0
        for uid in user_ids:
            user = db.session.get(User, uid)
            if not user:
                continue
            if action == "award":
                if award_badge(user, badge):
                    count += 1
            elif action == "revoke":
                if revoke_badge(user, badge):
                    count += 1
        verb = "vergeben" if action == "award" else "entzogen"
        flash(f"Badge '{badge.name}' bei {count} User(n) {verb}.", "success")
        return redirect(url_for("admin.badge_award", badge_id=badge_id))

    # Wer hat es schon?
    awarded_user_ids = {ub.user_id for ub in UserBadge.query.filter_by(badge_id=badge.id).all()}
    all_users = User.query.order_by(User.username).all()
    return render_template(
        "admin/badge_award.html",
        badge=badge, all_users=all_users,
        awarded_user_ids=awarded_user_ids,
    )


@admin_bp.route("/badges/recheck", methods=["POST"])
@login_required
@admin_required
def badge_recheck():
    """Erneut alle Auto-Vergabe-Regeln durchgehen (z.B. nach Schwellen-Änderung)."""
    check_and_award_badges()
    flash("Alle Badge-Regeln neu geprüft und ggf. vergeben.", "success")
    return redirect(url_for("admin.badges"))


@admin_bp.route("/special-questions", methods=["GET", "POST"])
@login_required
@admin_required
def special_questions():
    form = SpecialQuestionForm()
    if form.validate_on_submit():
        atype = form.answer_type.data or "text"
        opts_text = form.options.data or ""
        opt_list = [o.strip() for o in opts_text.split("\n") if o.strip()]

        # Optionen je nach Typ vorbereiten
        if atype == "choice":
            options_json = _json.dumps(opt_list) if opt_list else None
        elif atype == "yes_no":
            options_json = _json.dumps(["Ja", "Nein"])
        else:
            options_json = None  # team/multi_team/number/text brauchen keine

        # Korrekte Antwort verarbeiten
        correct = (form.correct_answer.data or "").strip() or None

        q = SpecialQuestion(
            text=form.text.data,
            description=form.description.data or None,
            answer_type=atype,
            options=options_json,
            multi_count=form.multi_count.data or 1,
            number_min=form.number_min.data,
            number_max=form.number_max.data,
            deadline=form.deadline.data,
            points_value=form.points_value.data,
            correct_answer=correct,
        )
        db.session.add(q)
        db.session.commit()
        evaluate_special_predictions()
        flash(f"Sonderfrage angelegt. Deadline: {q.deadline.strftime('%d.%m.%Y %H:%M')} Uhr.", "success")
        return redirect(url_for("admin.special_questions"))

    questions = SpecialQuestion.query.order_by(SpecialQuestion.deadline.desc()).all()
    min_dt = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    all_teams = Team.query.order_by(Team.name).all()
    return render_template(
        "admin/special_questions.html",
        form=form, questions=questions, min_dt=min_dt,
        all_teams=all_teams,
    )


@admin_bp.route("/special-question/<int:qid>/answer", methods=["POST"])
@login_required
@admin_required
def set_special_answer(qid):
    q = SpecialQuestion.query.get_or_404(qid)

    if q.answer_type == "multi_team":
        # Mehrere Werte aus Checkboxen
        values = [v.strip() for v in request.form.getlist("correct_answer") if v.strip()]
        q.correct_answer = _json.dumps(values) if values else None
    else:
        ans = request.form.get("correct_answer", "").strip()
        q.correct_answer = ans or None

    db.session.commit()
    evaluate_special_predictions()
    msg = f"Antwort für '{q.text[:40]}' gesetzt. Punkte wurden vergeben."
    flash(msg, "success")
    return redirect(url_for("admin.special_questions"))


@admin_bp.route("/special-question/<int:qid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_special_question(qid):
    q = SpecialQuestion.query.get_or_404(qid)
    SpecialPrediction.query.filter_by(question_id=qid).delete()
    db.session.delete(q)
    db.session.commit()
    flash("Sonderfrage gelöscht.", "info")
    return redirect(url_for("admin.special_questions"))


@admin_bp.route("/archive-season", methods=["POST"])
@login_required
@admin_required
def archive_current_season():
    label = request.form.get("season_label", "").strip()
    if not label:
        flash("Saison-Label fehlt (z.B. '2024/25').", "danger")
        return redirect(url_for("admin.dashboard"))
    archive_season(label)
    flash(f"Saison '{label}' archiviert. Sichtbar in der Ewigen Tabelle.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/settings/test-mail", methods=["POST"])
@login_required
@admin_required
def test_mail():
    """Sendet eine Testmail mit den aktuellen SMTP-Einstellungen."""
    recipient = (request.form.get("mail_test_recipient") or current_user.email or "").strip()
    if not recipient:
        flash("Bitte einen Test-Empfänger eintragen.", "danger")
        return redirect(url_for("admin.settings"))

    # Aktuelle Formularwerte vor Test übernehmen, damit der Button direkt nutzbar ist
    for key in ["mail_server", "mail_username", "mail_password", "mail_default_sender"]:
        if key in request.form:
            val = request.form.get(key, "").strip()
            if key == "mail_password" and not val:
                # Leeres Passwort im Test-Formular nicht ueberschreiben
                continue
            set_setting(key, val)
    if "mail_port" in request.form:
        try:
            set_setting("mail_port", int(request.form.get("mail_port") or 587))
        except ValueError:
            set_setting("mail_port", 587)
    set_setting("mail_use_tls", bool(request.form.get("mail_use_tls")))
    set_setting("mail_use_ssl", bool(request.form.get("mail_use_ssl")))
    apply_mail_settings()

    ok = send_email(
        "Testmail – Wulmstörper Tipprunde",
        [recipient],
        "Diese Testmail wurde über die SMTP-Einstellungen der Wulmstörper Tipprunde gesendet.\n\nWenn du diese Mail siehst, funktionieren E-Mail-Versand, Passwort-Reset und Erinnerungen grundsätzlich.",
    )
    if ok:
        flash(f"✅ Testmail wurde an {recipient} gesendet.", "success")
    else:
        flash("❌ Testmail konnte nicht gesendet werden. Prüfe SMTP-Server, Port, Benutzername, Passwort und Absender.", "danger")
    return redirect(url_for("admin.settings"))


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    form = SettingsForm()

    # ─── GET: Werte aus DB laden ───
    if request.method == "GET":
        form.points_exact.data = get_setting("points_exact", 4)
        form.points_diff.data = get_setting("points_diff", 3)
        form.points_tendency.data = get_setting("points_tendency", 2)
        form.pot_amount.data = get_setting("pot_amount", 5)
        form.pot_currency.data = get_setting("pot_currency", "€")
        form.pot_intro.data = get_setting("pot_intro", "")
        form.football_data_token.data = get_setting("football_data_token", "")
        form.mail_server.data = get_setting("mail_server", current_app.config.get("MAIL_SERVER", ""))
        form.mail_port.data = get_setting("mail_port", current_app.config.get("MAIL_PORT", 587))
        form.mail_username.data = get_setting("mail_username", current_app.config.get("MAIL_USERNAME", ""))
        form.mail_password.data = get_setting("mail_password", current_app.config.get("MAIL_PASSWORD", ""))
        form.mail_default_sender.data = get_setting("mail_default_sender", current_app.config.get("MAIL_DEFAULT_SENDER", ""))
        form.mail_use_tls.data = bool(get_setting("mail_use_tls", True))
        form.mail_use_ssl.data = bool(get_setting("mail_use_ssl", False))
        form.mail_test_recipient.data = current_user.email
        form.vapid_public.data = get_setting("vapid_public", "")
        form.vapid_private.data = get_setting("vapid_private", "")

    # ─── POST: Validieren + Speichern ───
    if form.validate_on_submit():
        old_exact = get_setting("points_exact", 4)
        old_diff = get_setting("points_diff", 3)
        old_tendency = get_setting("points_tendency", 2)

        # Punkte & Pott
        set_setting("points_exact", int(form.points_exact.data or 4))
        set_setting("points_diff", int(form.points_diff.data or 3))
        set_setting("points_tendency", int(form.points_tendency.data or 2))
        set_setting("pot_amount", int(form.pot_amount.data or 5))
        set_setting("pot_currency", (form.pot_currency.data or "€").strip())
        set_setting("pot_intro", (form.pot_intro.data or "").strip())

        # API Token
        api_token = (form.football_data_token.data or "").strip()
        if api_token:
            set_setting("football_data_token", api_token)

        # Mail Einstellungen
        set_setting("mail_server", (form.mail_server.data or "").strip())
        set_setting("mail_port", int(form.mail_port.data or 587))
        set_setting("mail_username", (form.mail_username.data or "").strip())

        mail_pwd = (form.mail_password.data or "").strip()
        if mail_pwd:
            set_setting("mail_password", mail_pwd)

        set_setting("mail_default_sender", (form.mail_default_sender.data or "").strip())
        set_setting("mail_use_tls", bool(form.mail_use_tls.data))
        set_setting("mail_use_ssl", bool(form.mail_use_ssl.data))

        # VAPID Keys
        vapid_pub = (form.vapid_public.data or "").strip()
        if vapid_pub:
            set_setting("vapid_public", vapid_pub)
        vapid_priv = (form.vapid_private.data or "").strip()
        if vapid_priv:
            set_setting("vapid_private", vapid_priv)

        apply_mail_settings()
        apply_vapid_settings()

        new_exact = int(form.points_exact.data or 4)
        new_diff = int(form.points_diff.data or 3)
        new_tendency = int(form.points_tendency.data or 2)

        if old_exact != new_exact or old_diff != new_diff or old_tendency != new_tendency:
            recalculate_all_points()
            flash("✅ Einstellungen gespeichert und Punkte neu berechnet.", "success")
        else:
            flash("✅ Einstellungen wurden erfolgreich gespeichert.", "success")

        return redirect(url_for("admin.settings"), code=303)

    elif request.method == "POST":
        # POST aber Validierung fehlgeschlagen → Fehler anzeigen
        for field_name, errors in form.errors.items():
            field_label = getattr(getattr(form, field_name, None), "label", None)
            label_text = field_label.text if field_label else field_name
            for err in errors:
                flash(f"❌ {label_text}: {err}", "danger")
        flash("⚠️ Einstellungen konnten nicht gespeichert werden. Bitte Felder prüfen.", "warning")

    # Flags für "Nicht konfiguriert"-Badges vorberechnen
    mail_missing = not (form.mail_server.data) or not (form.mail_username.data)
    api_missing = not (form.football_data_token.data)
    vapid_missing = not (form.vapid_public.data) or not (form.vapid_private.data)
    pts_missing = not (form.points_exact.data) or not (form.points_diff.data) or not (form.points_tendency.data)
    pot_missing = not (form.pot_amount.data) or form.pot_amount.data == 0

    return render_template("admin/settings.html", form=form,
                           mail_missing=mail_missing, api_missing=api_missing,
                           vapid_missing=vapid_missing, pts_missing=pts_missing,
                           pot_missing=pot_missing)


@admin_bp.route("/bots", methods=["GET", "POST"])
@login_required
@admin_required
def bots():
    from ai_opponent import ai_manager
    
    if request.method == "POST":
        action = request.form.get("action")
        if action == "tip":
            matchday_str = request.form.get("matchday")
            matchday = int(matchday_str) if matchday_str and matchday_str.strip() else None
            results = ai_manager.tip_all_matches(matchday=matchday)
            flash(f"Bots haben Tipps abgegeben! ({len(results)} Tipps generiert)", "success")
        elif action == "set_difficulty":
            bot_name = request.form.get("bot_name")
            difficulty = request.form.get("difficulty")
            if bot_name and difficulty:
                success = ai_manager.set_bot_difficulty(bot_name, difficulty)
                if success:
                    flash(f"Schwierigkeit für {bot_name} auf '{difficulty}' geändert.", "success")
                else:
                    flash(f"Konnte Schwierigkeit für {bot_name} nicht ändern.", "danger")
        elif action == "toggle_active":
            bot_name = request.form.get("bot_name")
            if bot_name:
                current = get_setting(f"bot_active_{bot_name}", True)
                set_setting(f"bot_active_{bot_name}", not current)
                flash(f"{bot_name} wurde {'aktiviert' if not current else 'deaktiviert'}.", "success")
        return redirect(url_for("admin.bots"))

    bots_data = ai_manager.get_rankings()
    # Aktive Status hinzufügen
    for bot in bots_data:
        bot['active'] = get_setting(f"bot_active_{bot['name']}", True)
    return render_template("admin/bots.html", bots=bots_data)


@admin_bp.route("/cache", methods=["GET", "POST"])
@login_required
@admin_required
def cache_page():
    from cache import cache
    
    if request.method == "POST":
        action = request.form.get("action")
        if action == "flush":
            success = cache.clear()
            if success:
                flash("Gesamter Cache wurde erfolgreich geleert!", "success")
            else:
                flash("Cache konnte nicht geleert werden oder ist inaktiv.", "warning")
        return redirect(url_for("admin.cache_page"))
        
    stats = cache.get_stats()
    return render_template("admin/cache.html", stats=stats)


# =========================================================== API (JSON) -
api_bp = Blueprint("api", __name__)


@api_bp.route("/leaderboard")
def api_leaderboard():
    rows = get_live_leaderboard()
    return jsonify([
        {"rank": r["rank"], "username": r["user"].username,
         "points": r["points"], "exact": r["exact"], "tips": r["tips"]}
        for r in rows
    ])


@api_bp.route("/live/standings")
@login_required
def api_live_standings():
    """Live-Tabelle von football-data.org (für Polling im Frontend)."""
    rows, err = fetch_live_standings()
    if not rows:
        return jsonify({"ok": False, "error": err}), 503
    return jsonify({
        "ok": True,
        "source": "football-data.org",
        "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
        "table": [
            {
                "rank": r["rank"],
                "team_name": r["team"].name,
                "team_short": r["team"].short_name,
                "team_logo": r["team"].logo,
                "played": r["played"], "won": r["won"], "drawn": r["drawn"], "lost": r["lost"],
                "goals_for": r["goals_for"], "goals_against": r["goals_against"],
                "goal_diff": r["goal_diff"], "points": r["points"],
                "form": r.get("form", ""),
            }
            for r in rows
        ],
    })


@api_bp.route("/tip/<int:match_id>", methods=["POST"])
@login_required
def api_save_tip(match_id):
    """Auto-Save eines Tipps via JSON. Wird beim Pfeil/Swipe/Joker-Klick getriggert.

    Joker-Verhalten: Wer den Joker hier setzt, bei dem wird ein
    bereits an anderer Stelle des Spieltags gesetzter Joker automatisch
    entfernt (Auto-Move). Statt einer Fehlermeldung gibt's einen Hinweis,
    wo der Joker vorher war.
    """
    match = Match.query.get_or_404(match_id)
    if not match.is_open():
        return jsonify({"ok": False, "error": "Anstoß bereits erfolgt"}), 400

    data = request.get_json(silent=True) or {}
    try:
        home_tip = int(data.get("home_tip"))
        away_tip = int(data.get("away_tip"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Ungültiger Tipp"}), 400

    if not (0 <= home_tip <= 30 and 0 <= away_tip <= 30):
        return jsonify({"ok": False, "error": "Score 0–30 erlaubt"}), 400

    use_joker = bool(data.get("joker", False))
    moved_from = None  # Info, falls Joker umgezogen wurde

    # Joker-Auto-Move: andere Joker am selben Spieltag entfernen
    if use_joker:
        existing = Prediction.query.join(Match).filter(
            Prediction.user_id == current_user.id,
            Prediction.joker.is_(True),
            Match.matchday == match.matchday,
            Prediction.match_id != match_id,
        ).all()
        for ep in existing:
            moved_from = f"{ep.match.home_team.short_name}-{ep.match.away_team.short_name}"
            ep.joker = False

    pred = Prediction.query.filter_by(user_id=current_user.id, match_id=match_id).first()
    if pred:
        pred.home_tip = home_tip
        pred.away_tip = away_tip
        pred.joker = use_joker
    else:
        pred = Prediction(
            user_id=current_user.id, match_id=match_id,
            home_tip=home_tip, away_tip=away_tip, joker=use_joker,
        )
        db.session.add(pred)
    db.session.commit()
    check_and_award_badges()
    
    # 🔥 PERFORMANCE: Cache invalidieren nach Tipp-Änderung
    try:
        from cache import invalidate_leaderboard, invalidate_match
        invalidate_leaderboard()
        invalidate_match(match_id)
    except Exception:
        pass  # Ignorieren wenn Cache nicht verfügbar
    
    return jsonify({
        "ok": True,
        "home_tip": pred.home_tip,
        "away_tip": pred.away_tip,
        "joker": pred.joker,
        "joker_moved_from": moved_from,    # für UI-Hinweis (optional)
    })


@api_bp.route("/live/center")
@login_required
def api_live_center():
    """All-in-one Endpunkt: Live-Matches von HEUTE + Tipp-Leaderboard.
    Wird vom Live-Center alle 30s gepollt."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Live-API-Pull: Spielstände frisch von football-data.org holen
    # (nutzt 30s Cache → schont Rate-Limit)
    today_matches = Match.query.filter(
        Match.kickoff >= today_start,
        Match.kickoff < today_end,
    ).all()
    matchdays = sorted(set(m.matchday for m in today_matches))

    sync_info = []
    for md in matchdays:
        res = fetch_live_match_updates(matchday=md)
        if res.get("ok"):
            sync_info.append(f"ST{md}: {res.get('updated', 0)} updates, {res.get('live', 0)} live")

    # Aktuelle Daten nach Sync
    matches = Match.query.filter(
        Match.kickoff >= today_start,
        Match.kickoff < today_end,
    ).order_by(Match.kickoff.asc()).all()

    # Tipp-Leaderboard (LIVE – berechnet laufende Spiele dynamisch)
    rows = get_live_leaderboard()

    # User-Tipps zu Live-Matches (für die Tipp-Anzeige)
    user_preds = {
        p.match_id: {"home_tip": p.home_tip, "away_tip": p.away_tip,
                     "joker": p.joker, "points": p.points}
        for p in Prediction.query.filter_by(user_id=current_user.id).all()
        if p.match_id in [m.id for m in matches]
    }

    return jsonify({
        "ok": True,
        "fetched_at": now.isoformat() + "Z",
        "sync_info": " | ".join(sync_info),
        "matches": [
            {
                "id": m.id,
                "matchday": m.matchday,
                "home_id": m.home_team_id,
                "home_name": m.home_team.name,
                "home_short": m.home_team.short_name,
                "home_logo": m.home_team.logo,
                "away_id": m.away_team_id,
                "away_name": m.away_team.name,
                "away_short": m.away_team.short_name,
                "away_logo": m.away_team.logo,
                "kickoff": m.kickoff.isoformat() + "Z",
                "home_score": m.home_score,
                "away_score": m.away_score,
                "status": m.status,
                "user_pred": user_preds.get(m.id),
            }
            for m in matches
        ],
        "leaderboard": [
            {
                "rank": r["rank"],
                "user_id": r["user"].id,
                "username": r["user"].username,
                "avatar": r["user"].avatar,
                "points": r["points"],
                "exact": r["exact"],
                "diff": r["diff"],
                "tendency": r["tendency"],
                "wrong": r["wrong"],
                "is_me": r["user"].id == current_user.id,
            }
            for r in rows
        ],
    })


@api_bp.route("/live/center/stream")
@login_required
def api_live_center_stream():
    """Server-Sent Events (SSE) Stream fuer Echtzeit-Updates des Live-Centers."""
    from flask import Response, stream_with_context
    import time
    import json as _json_lib

    def event_stream():
        last_state = None
        while True:
            # Hole aktuellen Zustand
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

            matches = Match.query.filter(
                Match.kickoff >= today_start,
                Match.kickoff < today_end,
            ).order_by(Match.kickoff.asc()).all()

            rows = get_live_leaderboard()

            user_preds = {
                p.match_id: {"home_tip": p.home_tip, "away_tip": p.away_tip,
                             "joker": p.joker, "points": p.points}
                for p in Prediction.query.filter_by(user_id=current_user.id).all()
                if p.match_id in [m.id for m in matches]
            }

            current_state = {
                "matches": [
                    {
                        "id": m.id,
                        "home_score": m.home_score,
                        "away_score": m.away_score,
                        "status": m.status,
                    }
                    for m in matches
                ],
                "leaderboard": [
                    {
                        "rank": r["rank"],
                        "user_id": r["user"].id,
                        "username": r["user"].username,
                        "points": r["points"],
                        "exact": r["exact"],
                        "diff": r["diff"],
                        "tendency": r["tendency"],
                    }
                    for r in rows
                ]
            }

            # Vergleiche mit letztem Zustand
            if current_state != last_state:
                # Ganze API Response simulieren damit das Frontend dieselbe Struktur hat
                payload = {
                    "ok": True,
                    "fetched_at": now.isoformat() + "Z",
                    "matches": [
                        {
                            "id": m.id,
                            "matchday": m.matchday,
                            "home_id": m.home_team_id,
                            "home_name": m.home_team.name,
                            "home_short": m.home_team.short_name,
                            "home_logo": m.home_team.logo,
                            "away_id": m.away_team_id,
                            "away_name": m.away_team.name,
                            "away_short": m.away_team.short_name,
                            "away_logo": m.away_team.logo,
                            "kickoff": m.kickoff.isoformat() + "Z",
                            "home_score": m.home_score,
                            "away_score": m.away_score,
                            "status": m.status,
                            "user_pred": user_preds.get(m.id),
                        }
                        for m in matches
                    ],
                    "leaderboard": [
                        {
                            "rank": r["rank"],
                            "user_id": r["user"].id,
                            "username": r["user"].username,
                            "avatar": r["user"].avatar,
                            "points": r["points"],
                            "exact": r["exact"],
                            "diff": r["diff"],
                            "tendency": r["tendency"],
                            "wrong": r["wrong"],
                            "is_me": r["user"].id == current_user.id,
                        }
                        for r in rows
                    ],
                }
                yield f"data: {_json_lib.dumps(payload)}\n\n"
                last_state = current_state

            time.sleep(5)  # Alle 5 Sekunden auf Aenderungen prüfen

    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@api_bp.route("/live/matchday/<int:matchday>")
@login_required
def api_live_matchday(matchday):
    """Live-Update aller Spiele eines Spieltags (für Polling)."""
    res = fetch_live_match_updates(matchday=matchday)
    matches = Match.query.filter_by(matchday=matchday).order_by(Match.kickoff).all()
    return jsonify({
        "ok": True,
        "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
        "updated": res.get("updated", 0),
        "live_count": res.get("live", 0),
        "matches": [
            {
                "id": m.id,
                "home": m.home_team.short_name,
                "away": m.away_team.short_name,
                "home_logo": m.home_team.logo,
                "away_logo": m.away_team.logo,
                "kickoff": m.kickoff.isoformat(),
                "home_score": m.home_score,
                "away_score": m.away_score,
                "status": m.status,
            }
            for m in matches
        ],
    })


@api_bp.route("/matches/<int:matchday>")
def api_matches(matchday):
    matches = Match.query.filter_by(matchday=matchday).all()
    return jsonify([
        {
            "id": m.id, "matchday": m.matchday,
            "home": m.home_team.name, "away": m.away_team.name,
            "home_logo": m.home_team.logo, "away_logo": m.away_team.logo,
            "kickoff": m.kickoff.isoformat(),
            "home_score": m.home_score, "away_score": m.away_score,
            "status": m.status,
        } for m in matches
    ])


@api_bp.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    sub = request.get_json()
    current_user.push_subscription = str(sub) if sub else None
    db.session.commit()
    return jsonify({"ok": True})


# ============================================================== MAIN -
app = create_app()



# ──────────────────────────────────────────────────────────────────────────────
# Zusätzliche Admin-Routen (KI-Bots, Cache, Neue Saison, WhatsApp Test)
# ──────────────────────────────────────────────────────────────────────────────
from admin_bots_routes import (
    _admin_bots_view, _admin_bots_tip_all,
    _admin_bots_tip_single, _admin_bots_reset, _admin_bots_toggle,
)
from cache_monitor_routes import (
    _admin_cache_view, _admin_cache_flush_all,
    _admin_cache_flush_pattern, _admin_cache_delete_key,
)


@admin_bp.route("/bots")
@login_required
@admin_required
def admin_bots():
    return _admin_bots_view()


@admin_bp.route("/bots/tip-all", methods=["POST"])
@login_required
@admin_required
def admin_bots_tip_all():
    return _admin_bots_tip_all()


@admin_bp.route("/bots/tip-single", methods=["POST"])
@login_required
@admin_required
def admin_bots_tip_single():
    return _admin_bots_tip_single()


@admin_bp.route("/bots/reset", methods=["POST"])
@login_required
@admin_required
def admin_bots_reset():
    return _admin_bots_reset()


@admin_bp.route("/bots/toggle", methods=["POST"])
@login_required
@admin_required
def admin_bots_toggle():
    return _admin_bots_toggle()


@admin_bp.route("/cache")
@login_required
@admin_required
def admin_cache():
    return _admin_cache_view()


@admin_bp.route("/cache/flush-all", methods=["POST"])
@login_required
@admin_required
def admin_cache_flush_all():
    return _admin_cache_flush_all()


@admin_bp.route("/cache/flush-pattern", methods=["POST"])
@login_required
@admin_required
def admin_cache_flush_pattern():
    return _admin_cache_flush_pattern()


@admin_bp.route("/cache/delete-key", methods=["POST"])
@login_required
@admin_required
def admin_cache_delete_key():
    return _admin_cache_delete_key()


@admin_bp.route("/new-season", methods=["GET", "POST"])
@login_required
@admin_required
def new_season():
    from extensions import db
    from models import (
        Match, Prediction, Comment, MatchdayWinner, SpecialQuestion,
        SpecialPrediction, SeasonArchive, User, Competition, CompetitionTeam,
    )
    from sqlalchemy import func

    if request.method == "POST":
        do_archive = request.form.get("do_archive") == "1"
        do_delete_schedule = request.form.get("do_delete_schedule") == "1"
        do_delete_specials = request.form.get("do_delete_specials") == "1"
        do_reset_bots = request.form.get("do_reset_bots") == "1"
        do_reset_badges = request.form.get("do_reset_badges") == "1"
        do_reset_paid = request.form.get("do_reset_paid") == "1"
        new_season_label = (request.form.get("new_season_label", "") or "2025/26").strip()

        season_code = get_setting("season", "2025")

        # 1. Archivieren
        if do_archive:
            rows = db.session.query(
                User.id.label("user_id"),
                func.coalesce(func.sum(Prediction.points), 0).label("points"),
                func.coalesce(func.sum(func.case((Prediction.points == 4, 1), else_=0)), 0).label("exact_count"),
            ).outerjoin(Prediction, Prediction.user_id == User.id).group_by(User.id).all()

            for r in rows:
                existing = SeasonArchive.query.filter_by(user_id=r.user_id, season=season_code).first()
                if existing:
                    existing.points = int(r.points or 0)
                    existing.exact_count = int(r.exact_count or 0)
                else:
                    db.session.add(SeasonArchive(
                        user_id=r.user_id, season=season_code,
                        points=int(r.points or 0), exact_count=int(r.exact_count or 0),
                        rank=0, diff_count=0,
                    ))
            db.session.commit()
            flash("✅ Ewige Tabelle aktualisiert.", "success")

        # 2. Spielplan löschen
        if do_delete_schedule:
            MatchdayWinner.query.filter_by(season=season_code).delete(synchronize_session=False)
            Comment.query.delete(synchronize_session=False)
            Prediction.query.delete(synchronize_session=False)
            Match.query.delete(synchronize_session=False)
            db.session.commit()
            flash("🗑️ Spielplan, Tipps, Kommentare und Spieltagsieger gelöscht.", "success")

        # 3. Sonderfragen
        if do_delete_specials:
            SpecialPrediction.query.delete(synchronize_session=False)
            SpecialQuestion.query.delete(synchronize_session=False)
            db.session.commit()
            flash("🗑️ Sonderfragen und -tipps gelöscht.", "success")

        # 4. Bot-Tipps zurücksetzen
        if do_reset_bots:
            bot_ids = [u.id for u in User.query.filter(User.email.like("%@bot.local")).all()]
            if bot_ids:
                Prediction.query.filter(Prediction.user_id.in_(bot_ids)).delete(synchronize_session=False)
                db.session.commit()
            flash("🤖 Bot-Tipps zurückgesetzt.", "success")

        # 5. Badges zurücksetzen
        if do_reset_badges:
            from models import UserBadge
            UserBadge.query.delete(synchronize_session=False)
            db.session.commit()
            flash("🏅 Spieler-Badges zurückgesetzt.", "success")

        # 6. Bezahlstatus
        if do_reset_paid:
            User.query.filter(~User.email.like("%@bot.local")).update({
                "has_paid": False, "paid_at": None, "paid_note": None
            }, synchronize_session=False)
            db.session.commit()
            flash("💰 Bezahlstatus zurückgesetzt.", "success")

        # 7. Neue Competition
        old_comp = Competition.query.filter_by(is_active=True).first()
        if old_comp:
            old_comp.is_active = False
        new_comp = Competition(
            code="BL1", name=f"Bundesliga {new_season_label}",
            season=new_season_label, matchdays=34, teams_count=18,
            is_active=True, external_id=None,
        )
        db.session.add(new_comp)
        db.session.commit()
        set_setting("season", new_season_label)
        flash(f"🏁 Neue Saison '{new_season_label}' gestartet.", "success")
        return redirect(url_for("admin.new_season"), code=303)

    return render_template("admin/new_season.html")


@app.route("/profile/test-whatsapp", methods=["POST"])
@login_required
def test_whatsapp():
    from whatsapp import send_whatsapp_test
    success = send_whatsapp_test(current_user)
    if success:
        flash("✅ WhatsApp-Testnachricht gesendet! Schau auf dein Handy.", "success")
    else:
        flash("❌ Senden fehlgeschlagen. Nummer und API-Key prüfen.", "error")
    return redirect(url_for("profile"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port, host="0.0.0.0")
