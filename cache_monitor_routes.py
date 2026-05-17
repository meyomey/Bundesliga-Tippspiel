"""Cache-Monitoring Routes für den Admin-Bereich."""
from flask import render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_required, current_user


def _get_redis():
    try:
        import redis as redis_lib
        redis_url = current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url, decode_responses=True, socket_timeout=2)
        r.ping()
        return r, None
    except Exception as e:
        return None, str(e)


def _admin_cache_view():
    r, error = _get_redis()
    redis_ok = r is not None
    stats = {}
    all_keys = []
    redis_info = {}
    if redis_ok:
        try:
            info = r.info()
            redis_info = {k: v for k, v in info.items() if k in (
                "redis_version", "uptime_in_seconds", "used_memory_human",
                "maxmemory_human", "connected_clients", "total_commands_processed",
                "keyspace_hits", "keyspace_misses", "role", "os", "tcp_port", "hz",
            )}
            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            total = hits + misses
            hit_rate = round(hits / total * 100, 1) if total > 0 else 0.0
            uptime_days = int(info.get("uptime_in_seconds", 0)) // 86400
            stats = {
                "hit_rate": hit_rate,
                "keyspace_hits": hits,
                "keyspace_misses": misses,
                "used_memory_human": info.get("used_memory_human", "–"),
                "total_keys": r.dbsize(),
                "uptime_days": uptime_days,
            }
            keys_raw = r.keys("*")[:500]
            for k in sorted(keys_raw):
                ttl = r.ttl(k)
                all_keys.append({"name": k, "ttl": ttl})
        except Exception as e:
            redis_ok = False
            error = str(e)
    return render_template(
        "admin/cache.html", redis_ok=redis_ok, redis_error=error,
        stats=stats, all_keys=all_keys, redis_info=redis_info,
    )


def _admin_cache_flush_all():
    r, error = _get_redis()
    if r:
        try:
            r.flushdb()
            flash("💣 Cache vollständig geleert.", "success")
        except Exception as e:
            flash(f"❌ Fehler: {e}", "error")
    else:
        flash(f"❌ Redis nicht verfügbar: {error}", "error")
    return redirect(url_for("admin.cache"))


def _admin_cache_flush_pattern():
    pattern = request.form.get("pattern", "").strip()
    if not pattern:
        flash("❌ Kein Muster angegeben.", "error")
        return redirect(url_for("admin.cache"))
    r, error = _get_redis()
    if r:
        try:
            keys = r.keys(pattern)
            if keys:
                r.delete(*keys)
                flash(f"🗑️ {len(keys)} Keys mit Muster '{pattern}' gelöscht.", "success")
            else:
                flash(f"ℹ️ Keine Keys mit Muster '{pattern}' gefunden.", "info")
        except Exception as e:
            flash(f"❌ Fehler: {e}", "error")
    else:
        flash(f"❌ Redis nicht verfügbar: {error}", "error")
    return redirect(url_for("admin.cache"))


def _admin_cache_delete_key():
    key = request.form.get("key", "").strip()
    if not key:
        flash("❌ Kein Key angegeben.", "error")
        return redirect(url_for("admin.cache"))
    r, error = _get_redis()
    if r:
        try:
            deleted = r.delete(key)
            if deleted:
                flash(f"🗑️ Key '{key}' gelöscht.", "success")
            else:
                flash(f"ℹ️ Key '{key}' nicht gefunden.", "info")
        except Exception as e:
            flash(f"❌ Fehler: {e}", "error")
    else:
        flash(f"❌ Redis nicht verfügbar: {error}", "error")
    return redirect(url_for("admin.cache"))
