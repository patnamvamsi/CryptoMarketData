"""
Redis cache client with connection pooling, metrics tracking, and graceful error handling.

This module provides a centralized Redis cache implementation for the CryptoMarketData service.
It implements the Cache-Aside pattern with automatic serialization and fallback mechanisms.
"""

import json
import logging
import redis
from typing import Any, Optional, Union
from datetime import datetime, date
import traceback

logger = logging.getLogger(__name__)

# Global singleton instance
_global_redis_cache = None


class RedisCache:
    """
    Redis cache client with connection pooling and metrics tracking.

    Features:
    - Connection pooling for efficient resource usage
    - JSON serialization for complex data types
    - Metrics tracking (hits, misses, errors)
    - Graceful error handling with fallback
    - Pattern-based key deletion
    - Health checks
    """

    def __init__(self, host='localhost', port=6379, db=0, password=None,
                 socket_timeout=5, socket_connect_timeout=2, enabled=True):
        """
        Initialize Redis cache with connection pool.

        Args:
            host: Redis host address
            port: Redis port number
            db: Redis database number
            password: Redis password (None for no auth)
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Socket connect timeout in seconds
            enabled: Whether caching is enabled
        """
        self.enabled = enabled
        self.host = host
        self.port = port
        self.db = db

        # Metrics
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.last_error = None
        self.last_error_time = None

        # Circuit breaker
        self.consecutive_errors = 0
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60  # seconds
        self.circuit_open_time = None

        if not self.enabled:
            logger.info("Redis cache is disabled")
            self.client = None
            return

        try:
            # Create connection pool
            pool = redis.ConnectionPool(
                host=host,
                port=port,
                db=db,
                password=password,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                decode_responses=True,  # Auto-decode bytes to strings
                max_connections=10
            )

            self.client = redis.Redis(connection_pool=pool)

            # Test connection
            self.client.ping()
            logger.info(f"Redis cache connected to {host}:{port} (db={db})")

        except Exception as e:
            logger.error(f"Failed to initialize Redis cache: {e}")
            logger.error(traceback.format_exc())
            self.client = None
            self.enabled = False

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open (too many consecutive errors)."""
        if self.consecutive_errors < self.circuit_breaker_threshold:
            return False

        if self.circuit_open_time is None:
            self.circuit_open_time = datetime.now()
            logger.warning(f"Circuit breaker OPEN - too many Redis errors ({self.consecutive_errors})")
            return True

        # Check if timeout has passed
        elapsed = (datetime.now() - self.circuit_open_time).total_seconds()
        if elapsed > self.circuit_breaker_timeout:
            logger.info("Circuit breaker timeout elapsed, resetting")
            self.consecutive_errors = 0
            self.circuit_open_time = None
            return False

        return True

    def _record_error(self, error: Exception):
        """Record error metrics and circuit breaker state."""
        self.errors += 1
        self.consecutive_errors += 1
        self.last_error = str(error)
        self.last_error_time = datetime.now()

    def _record_success(self):
        """Reset consecutive error counter on successful operation."""
        self.consecutive_errors = 0
        if self.circuit_open_time:
            logger.info("Circuit breaker CLOSED - Redis recovered")
            self.circuit_open_time = None

    def _serialize(self, value: Any) -> str:
        """
        Serialize value for storage in Redis.

        Handles:
        - None -> "null"
        - bool, int, float, str -> JSON string
        - datetime, date -> ISO format string
        - list, dict -> JSON string

        Args:
            value: Value to serialize

        Returns:
            JSON string representation
        """
        def json_serial(obj):
            """JSON serializer for datetime objects."""
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        try:
            return json.dumps(value, default=json_serial)
        except (TypeError, ValueError) as e:
            logger.warning(f"Serialization failed, using string representation: {e}")
            return str(value)

    def _deserialize(self, value: Optional[str]) -> Any:
        """
        Deserialize value from Redis.

        Args:
            value: JSON string from Redis

        Returns:
            Deserialized Python object
        """
        if value is None:
            return None

        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # Return as-is if not valid JSON
            return value

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/error
        """
        if not self.enabled or self.client is None or self._is_circuit_open():
            return None

        try:
            value = self.client.get(key)
            if value is not None:
                self.hits += 1
                self._record_success()
                return self._deserialize(value)
            else:
                self.misses += 1
                return None

        except Exception as e:
            self._record_error(e)
            logger.warning(f"Redis GET error for key '{key}': {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (None for no expiry)

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or self.client is None or self._is_circuit_open():
            return False

        try:
            # Check size limit (1MB)
            serialized = self._serialize(value)
            if len(serialized) > 1_000_000:
                logger.warning(f"Value too large to cache ({len(serialized)} bytes): {key}")
                return False

            if ttl is not None:
                self.client.setex(key, ttl, serialized)
            else:
                self.client.set(key, serialized)

            self._record_success()
            return True

        except Exception as e:
            self._record_error(e)
            logger.warning(f"Redis SET error for key '{key}': {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete a single key from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or self.client is None or self._is_circuit_open():
            return False

        try:
            self.client.delete(key)
            self._record_success()
            return True

        except Exception as e:
            self._record_error(e)
            logger.warning(f"Redis DELETE error for key '{key}': {e}")
            return False

    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching a pattern.

        Args:
            pattern: Key pattern (e.g., "cryptomarket:active_symbols:*")

        Returns:
            Number of keys deleted
        """
        if not self.enabled or self.client is None or self._is_circuit_open():
            return 0

        try:
            keys = self.client.keys(pattern)
            if keys:
                deleted = self.client.delete(*keys)
                self._record_success()
                return deleted
            return 0

        except Exception as e:
            self._record_error(e)
            logger.warning(f"Redis DELETE_PATTERN error for pattern '{pattern}': {e}")
            return 0

    def clear_all(self) -> bool:
        """
        Clear all keys in the current database.

        WARNING: This clears ALL keys in the Redis database.

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or self.client is None or self._is_circuit_open():
            return False

        try:
            self.client.flushdb()
            self._record_success()
            logger.info("Cleared all cache data")
            return True

        except Exception as e:
            self._record_error(e)
            logger.error(f"Redis FLUSHDB error: {e}")
            return False

    def health_check(self) -> bool:
        """
        Check if Redis connection is healthy.

        Returns:
            True if Redis is accessible, False otherwise
        """
        if not self.enabled or self.client is None:
            return False

        try:
            self.client.ping()
            self._record_success()
            return True
        except Exception as e:
            self._record_error(e)
            logger.warning(f"Redis health check failed: {e}")
            return False

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache metrics
        """
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        stats = {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "total_requests": total_requests,
            "hit_rate": round(hit_rate, 2),
            "consecutive_errors": self.consecutive_errors,
            "circuit_breaker_open": self._is_circuit_open()
        }

        # Add info from Redis if available
        if self.enabled and self.client is not None:
            try:
                info = self.client.info()
                stats["total_keys"] = info.get("db0", {}).get("keys", 0) if "db0" in info else 0
                stats["memory_used_mb"] = round(info.get("used_memory", 0) / 1024 / 1024, 2)
                stats["uptime_seconds"] = info.get("uptime_in_seconds", 0)
            except Exception as e:
                logger.warning(f"Failed to get Redis info: {e}")

        if self.last_error:
            stats["last_error"] = self.last_error
            stats["last_error_time"] = self.last_error_time.isoformat() if self.last_error_time else None

        return stats


# Singleton pattern functions
def get_redis_cache() -> Optional[RedisCache]:
    """
    Get the global Redis cache instance.

    Returns:
        RedisCache instance or None if not initialized
    """
    return _global_redis_cache


def set_redis_cache(cache: RedisCache):
    """
    Set the global Redis cache instance.

    Args:
        cache: RedisCache instance to set as global
    """
    global _global_redis_cache
    _global_redis_cache = cache
