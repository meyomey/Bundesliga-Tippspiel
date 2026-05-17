"""Redis Caching Integration fuer die Wulmstörper Tipprunde.

Bietet:
- Ranglisten-Caching (5 Minuten)
- API-Response Caching
- Session-Storage (optional)
- Cache-Invalidation bei Aenderungen
"""
import json
import pickle
from functools import wraps
from datetime import datetime, timedelta
from typing import Any, Optional, Callable

from flask import current_app

try:
    import redis
except ImportError:
    redis = None


class CacheManager:
    """Zentrale Cache-Verwaltung mit Redis- oder Dummy-Backend."""
    
    def __init__(self):
        self._redis: Optional[Any] = None
        self._enabled = False
        self._default_ttl = 300  # 5 Minuten
    
    def init_app(self, app):
        """Initialisiert Redis aus App-Config."""
        if redis is None:
            app.logger.warning("⚠️ Das Python-Modul 'redis' ist nicht installiert. Cache ist deaktiviert.")
            self._redis = None
            self._enabled = False
            return
            
        redis_url = app.config.get('REDIS_URL')
        if redis_url:
            try:
                # Socket-Timeouts auf 1s setzen, damit die App nicht hängt wenn Redis aus ist
                self._redis = redis.from_url(redis_url, decode_responses=False, 
                                             socket_timeout=1, socket_connect_timeout=1)
                self._redis.ping()
                self._enabled = True
                app.logger.info(f"✅ Redis Cache verbunden: {redis_url}")
            except Exception as e:
                app.logger.warning(f"⚠️ Redis nicht verfuegbar: {e}. Cache deaktiviert.")
                self._redis = None
                self._enabled = False
        else:
            app.logger.info("ℹ️ Kein REDIS_URL konfiguriert. Cache deaktiviert.")
    
    def get(self, key: str) -> Optional[Any]:
        """Holt Wert aus Cache."""
        if not self._enabled or not self._redis:
            return None
        try:
            data = self._redis.get(key)
            if data:
                return pickle.loads(data)
        except Exception:
            pass
        return None
    
    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """Speichert Wert im Cache."""
        if not self._enabled or not self._redis:
            return False
        try:
            ttl = ttl or self._default_ttl
            serialized = pickle.dumps(value)
            self._redis.setex(key, ttl, serialized)
            return True
        except Exception:
            return False
    
    def delete(self, key: str) -> bool:
        """Loescht einen Key."""
        if not self._enabled or not self._redis:
            return False
        try:
            self._redis.delete(key)
            return True
        except Exception:
            return False
    
    def delete_pattern(self, pattern: str) -> int:
        """Loescht alle Keys matching pattern."""
        if not self._enabled or not self._redis:
            return 0
        try:
            keys = self._redis.keys(pattern)
            if keys:
                return self._redis.delete(*keys)
        except Exception:
            pass
        return 0
    
    def clear(self) -> bool:
        """Leert den gesamten Cache."""
        if not self._enabled or not self._redis:
            return False
        try:
            self._redis.flushdb()
            return True
        except Exception:
            return False
    
    def get_stats(self) -> dict:
        """Liefert Cache-Statistiken."""
        if not self._enabled or not self._redis:
            return {"enabled": False}
        try:
            info = self._redis.info()
            return {
                "enabled": True,
                "keys": self._redis.dbsize(),
                "used_memory_human": info.get("used_memory_human", "N/A"),
                "connected_clients": info.get("connected_clients", 0),
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}


# Globaler Cache-Manager
cache = CacheManager()


def cached(ttl: int = 300, key_prefix: str = None, key_builder: Callable = None):
    """Decorator fuer Funktions-Caching.
    
    Args:
        ttl: Cache-Dauer in Sekunden
        key_prefix: Prefix fuer Cache-Key
        key_builder: Funktion um Key zu bauen (erhaelt *args, **kwargs)
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Cache-Key bauen
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                func_name = f.__name__
                prefix = key_prefix or func_name
                arg_str = ":".join(str(a) for a in args[1:] if not callable(a))  # self跳过
                kwarg_str = ":".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = f"{prefix}:{arg_str}:{kwarg_str}" if arg_str or kwarg_str else prefix
            
            # Versuche aus Cache zu lesen
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                current_app.logger.debug(f"🎯 Cache HIT: {cache_key}")
                return cached_value
            
            # Funktion ausfuehren und cachen
            result = f(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            current_app.logger.debug(f"💾 Cache SET: {cache_key}")
            return result
        
        # Cache-Invalidation Funktion anhaengen
        wrapper.cache_key = lambda *a, **kw: key_builder(*a, **kw) if key_builder else f.__name__
        wrapper.invalidate = lambda *a, **kw: cache.delete(wrapper.cache_key(*a, **kw))
        wrapper.invalidate_pattern = lambda pattern: cache.delete_pattern(pattern)
        
        return wrapper
    return decorator


def invalidate_leaderboard():
    """Invalidiert alle Ranglisten-Caches (nach Tipp-Abgabe oder Ergebnis-Update)."""
    deleted = cache.delete_pattern("leaderboard:*")
    cache.delete_pattern("standings:*")
    cache.delete("stats:*")
    current_app.logger.info(f"🗑️ Leaderboard-Cache invalidiert ({deleted} Keys)")
    return deleted


def invalidate_match(match_id: int):
    """Invalidiert Cache fuer ein bestimmtes Spiel."""
    cache.delete(f"match:{match_id}")
    cache.delete_pattern(f"tips:match:{match_id}:*")


# Hilfs-Funktionen fuer haefige Cache-Patterns
def cache_key_leaderboard(matchday: int = None, season: str = None) -> str:
    """Baut Cache-Key fuer Rangliste."""
    return f"leaderboard:{season or 'current'}:{matchday or 'total'}"


def cache_key_user_stats(user_id: int, season: str = None) -> str:
    """Baut Cache-Key fuer User-Statistiken."""
    return f"stats:user:{user_id}:{season or 'current'}"


def cache_key_match_detail(match_id: int) -> str:
    """Baut Cache-Key fuer Spiel-Details."""
    return f"match:{match_id}"
