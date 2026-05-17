"""Tests fuer Redis Caching."""
import pytest
from unittest.mock import Mock, MagicMock

from cache import CacheManager, cached, invalidate_leaderboard


class TestCacheManager:
    """Test cases fuer Cache Manager."""
    
    def test_cache_disabled_without_redis(self, app):
        """Test: Cache ist deaktiviert ohne Redis-URL."""
        app.config['REDIS_URL'] = None
        cache = CacheManager()
        cache.init_app(app)
        
        assert cache._enabled is False
        assert cache.get('any_key') is None
    
    def test_cache_operations_with_mock(self, app):
        """Test: Cache-Operationen mit Mock-Redis."""
        mock_redis = Mock()
        mock_redis.ping.return_value = True
        mock_redis.get.return_value = None
        mock_redis.setex.return_value = True
        
        app.config['REDIS_URL'] = 'redis://localhost:6379/0'
        cache = CacheManager()
        cache._redis = mock_redis
        cache._enabled = True
        
        # Test set/get
        cache.set('test_key', {'data': 'value'}, ttl=60)
        mock_redis.setex.assert_called_once()
    
    def test_cache_delete_pattern(self, app):
        """Test: Pattern-basiertes Loeschen."""
        mock_redis = Mock()
        mock_redis.keys.return_value = ['key1', 'key2']
        mock_redis.delete.return_value = 2
        
        app.config['REDIS_URL'] = 'redis://localhost:6379/0'
        cache = CacheManager()
        cache._redis = mock_redis
        cache._enabled = True
        
        deleted = cache.delete_pattern('test:*')
        
        assert deleted == 2
        mock_redis.keys.assert_called_with('test:*')


class TestCachedDecorator:
    """Test cases fuer @cached Decorator."""
    
    def test_cached_function_uses_cache(self, app):
        """Test: Decorator verwendet Cache."""
        with app.app_context():
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = True
            
            # Cache-Manager patchen
            import cache as cache_module
            original_cache = cache_module.cache
            cache_module.cache = mock_cache
            
            call_count = 0
            
            @cached(ttl=300, key_prefix='test')
            def expensive_function(x, y):
                nonlocal call_count
                call_count += 1
                return x + y
            
            # Erster Aufruf
            result1 = expensive_function(1, 2)
            assert result1 == 3
            assert call_count == 1
            
            # Cache zuruecksetzen fuer zweiten Test
            mock_cache.get.return_value = 42
            
            # Zweiter Aufruf (sollte aus Cache kommen)
            result2 = expensive_function(1, 2)
            assert result2 == 42  # Aus Cache
            assert call_count == 1  # Nicht erneut aufgerufen
            
            # Cache wiederherstellen
            cache_module.cache = original_cache
    
    def test_cached_with_key_builder(self, app):
        """Test: Custom Key Builder."""
        with app.app_context():
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache.set.return_value = True
            
            import cache as cache_module
            original_cache = cache_module.cache
            cache_module.cache = mock_cache
            
            def custom_key(x, y, **kwargs):
                return f"custom:{x}:{y}:{kwargs.get('z', 0)}"
            
            @cached(ttl=60, key_builder=custom_key)
            def my_func(x, y, z=0):
                return x + y + z
            
            my_func(1, 2, z=3)
            
            # Pruefe dass Custom-Key verwendet wurde
            mock_cache.set.assert_called_once()
            call_args = mock_cache.set.call_args
            assert 'custom:1:2:3' in str(call_args)
            
            cache_module.cache = original_cache


class TestCacheInvalidation:
    """Test cases fuer Cache Invalidation."""
    
    def test_invalidate_leaderboard(self, app):
        """Test: Leaderboard-Cache wird invalidiert."""
        with app.app_context():
            mock_cache = MagicMock()
            mock_cache.delete_pattern.return_value = 5
            
            import cache as cache_module
            original_cache = cache_module.cache
            cache_module.cache = mock_cache
            
            deleted = invalidate_leaderboard()
            
            assert deleted == 5
            assert mock_cache.delete_pattern.call_count >= 2
            
            cache_module.cache = original_cache
