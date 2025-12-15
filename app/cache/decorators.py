"""
Cache decorators for applying Redis caching to functions.

This module provides decorators that implement the Cache-Aside pattern,
automatically caching function results and retrieving from cache when available.
"""

import functools
import hashlib
import logging
from typing import Callable, Optional, Any
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def cache_result(key_pattern: str, ttl: int = 300, enabled_check: Optional[Callable[[], bool]] = None):
    """
    Decorator to cache function results in Redis.

    Uses the Cache-Aside pattern:
    1. Check cache for existing value
    2. If found (cache hit), return cached value
    3. If not found (cache miss), execute function
    4. Store result in cache for future requests

    Args:
        key_pattern: Template for cache key with placeholders for function parameters.
                     Example: "cryptomarket:active_symbols:{exchange}:{active}"
                     Use parameter names in curly braces: {param_name}
        ttl: Time to live in seconds (None for no expiry)
        enabled_check: Optional function to check if caching is enabled.
                       Example: lambda: config.ENABLE_REDIS_CACHE

    Usage:
        @cache_result(key_pattern="user:{user_id}", ttl=300)
        def get_user(user_id):
            return database.fetch_user(user_id)

    Notes:
        - SQLAlchemy Session objects are automatically excluded from cache keys
        - Complex objects are hashed if they can't be converted to strings
        - Function arguments must be JSON-serializable for cache key generation
        - Cache failures are logged but don't affect function execution
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Check if caching is enabled
            if enabled_check and not enabled_check():
                logger.debug(f"Cache disabled for {func.__name__}, executing function")
                return func(*args, **kwargs)

            # Get Redis cache instance
            from app.cache.redis_client import get_redis_cache
            cache = get_redis_cache()

            if not cache or not cache.enabled:
                # Cache not available, execute function normally
                return func(*args, **kwargs)

            # Generate cache key from function arguments
            cache_key = None
            try:
                cache_key = _generate_cache_key(key_pattern, func, args, kwargs)
                logger.debug(f"Generated cache key: {cache_key}")
            except Exception as e:
                logger.warning(f"Failed to generate cache key for {func.__name__}: {e}")
                # Can't generate key, execute function without caching
                return func(*args, **kwargs)

            # Try to get from cache
            try:
                cached_value = cache.get(cache_key)
                if cached_value is not None:
                    logger.debug(f"Cache HIT for {func.__name__}: {cache_key}")
                    return cached_value
                else:
                    logger.debug(f"Cache MISS for {func.__name__}: {cache_key}")
            except Exception as e:
                logger.warning(f"Cache GET failed for {func.__name__}: {e}")
                # Cache read failed, continue to execute function

            # Execute function (cache miss or error)
            result = func(*args, **kwargs)

            # Store result in cache
            try:
                if result is not None:  # Only cache non-None results
                    cache.set(cache_key, result, ttl=ttl)
                    logger.debug(f"Cached result for {func.__name__}: {cache_key} (TTL={ttl}s)")
            except Exception as e:
                logger.warning(f"Cache SET failed for {func.__name__}: {e}")
                # Cache write failed, but we have the result, so continue

            return result

        return wrapper

    return decorator


def _generate_cache_key(key_pattern: str, func: Callable, args: tuple, kwargs: dict) -> str:
    """
    Generate cache key from key pattern and function arguments.

    Args:
        key_pattern: Cache key template with {param_name} placeholders
        func: Function being cached
        args: Positional arguments passed to function
        kwargs: Keyword arguments passed to function

    Returns:
        Generated cache key string

    Raises:
        ValueError: If required parameters are missing or invalid
    """
    # Get function parameter names
    import inspect
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    # Build parameter dictionary from args and kwargs
    params = {}

    # Add positional arguments
    for i, arg in enumerate(args):
        if i < len(param_names):
            param_name = param_names[i]
            # Skip SQLAlchemy Session objects
            if not isinstance(arg, Session):
                params[param_name] = arg

    # Add keyword arguments
    for key, value in kwargs.items():
        if key != 'session' and not isinstance(value, Session):
            params[key] = value

    # Replace placeholders in key pattern
    cache_key = key_pattern

    # Find all placeholders in the pattern: {param_name}
    import re
    placeholders = re.findall(r'\{(\w+)\}', key_pattern)

    for placeholder in placeholders:
        if placeholder in params:
            value = params[placeholder]
            # Convert value to string representation
            str_value = _param_to_string(value)
            cache_key = cache_key.replace(f"{{{placeholder}}}", str_value)
        else:
            # Placeholder not found in parameters - might be None or optional
            # Replace with empty string or "None"
            logger.debug(f"Parameter '{placeholder}' not found, using 'None'")
            cache_key = cache_key.replace(f"{{{placeholder}}}", "None")

    return cache_key


def _param_to_string(param: Any) -> str:
    """
    Convert parameter to string representation for cache key.

    Args:
        param: Parameter value

    Returns:
        String representation of parameter

    Notes:
        - None -> "None"
        - bool -> "true" or "false" (lowercase)
        - Numbers and strings -> str(param)
        - Lists, tuples -> hash of sorted items
        - Dicts -> hash of sorted items
        - Other objects -> hash of string representation
    """
    if param is None:
        return "None"
    elif isinstance(param, bool):
        return "true" if param else "false"
    elif isinstance(param, (int, float, str)):
        return str(param)
    elif isinstance(param, (list, tuple)):
        # Hash the sorted list to keep key stable
        try:
            sorted_items = sorted(param)
            items_str = ",".join(str(item) for item in sorted_items)
            return hashlib.md5(items_str.encode()).hexdigest()[:16]
        except TypeError:
            # Can't sort (mixed types), hash as-is
            items_str = ",".join(str(item) for item in param)
            return hashlib.md5(items_str.encode()).hexdigest()[:16]
    elif isinstance(param, dict):
        # Hash sorted dict items
        items_str = ",".join(f"{k}:{v}" for k, v in sorted(param.items()))
        return hashlib.md5(items_str.encode()).hexdigest()[:16]
    else:
        # For other objects, hash their string representation
        return hashlib.md5(str(param).encode()).hexdigest()[:16]
