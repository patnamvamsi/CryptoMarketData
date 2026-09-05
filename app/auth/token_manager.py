"""
Zerodha Token Manager - Shared token storage via Redis

This module provides centralized management of Zerodha access tokens,
enabling token sharing across multiple microservices.

Token Flow:
1. Market-data service authenticates and stores token in Redis
2. Other services (webserver, etc.) retrieve token from Redis
3. Token auto-expires after 24 hours (Zerodha's validity period)

Redis Key Structure:
- Key: "zerodha:access_token"
- Value: JSON with {access_token, user_id, generated_at, expires_at}
- TTL: 86400 seconds (24 hours)
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ZerodhaTokenManager:
    """
    Manages Zerodha access tokens in Redis for cross-service sharing.

    Features:
    - Store access tokens with metadata
    - Retrieve tokens with expiry validation
    - Automatic expiration after 24 hours
    - Thread-safe Redis operations
    """

    REDIS_KEY = "zerodha:access_token"
    TOKEN_TTL = 86400  # 24 hours in seconds (Zerodha token validity)

    def __init__(self, redis_client):
        """
        Initialize token manager with Redis client.

        Args:
            redis_client: RedisCache instance from app/cache/redis_client.py
        """
        self.redis = redis_client

    def store_token(
        self,
        access_token: str,
        user_id: str,
        profile: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store Zerodha access token in Redis with metadata.

        Args:
            access_token: Zerodha access token string
            user_id: Zerodha user ID
            profile: Optional user profile data from KiteConnect

        Returns:
            bool: True if stored successfully, False otherwise

        Example:
            >>> manager.store_token("xyz123", "AB1234", {"email": "user@example.com"})
            True
        """
        try:
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=self.TOKEN_TTL)

            token_data = {
                "access_token": access_token,
                "user_id": user_id,
                "generated_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "profile": profile or {}
            }

            # Store in Redis with TTL
            # Note: RedisCache.set() already serializes to JSON, so pass dict directly
            success = self.redis.set(
                self.REDIS_KEY,
                token_data,
                ttl=self.TOKEN_TTL
            )

            if success:
                logger.info(
                    f"Zerodha token stored in Redis for user {user_id}, "
                    f"expires at {expires_at.isoformat()}"
                )
            else:
                logger.error("Failed to store Zerodha token in Redis")

            return success

        except Exception as e:
            logger.error(f"Error storing Zerodha token: {e}", exc_info=True)
            return False

    def get_token(self) -> Optional[str]:
        """
        Retrieve Zerodha access token from Redis.

        Returns:
            str: Access token if valid, None if not found or expired

        Example:
            >>> token = manager.get_token()
            >>> if token:
            ...     kite.set_access_token(token)
        """
        try:
            # RedisCache.get() automatically deserializes JSON
            data = self.redis.get(self.REDIS_KEY)

            if not data:
                logger.debug("No Zerodha token found in Redis")
                return None

            # data is already a dict (deserialized by RedisCache)
            access_token = data.get("access_token")

            if not access_token:
                logger.warning("Zerodha token data in Redis is malformed")
                return None

            # Check expiration
            expires_at_str = data.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                if datetime.utcnow() > expires_at:
                    logger.warning("Zerodha token in Redis has expired")
                    self.delete_token()
                    return None

            logger.debug(f"Retrieved Zerodha token for user {data.get('user_id')}")
            return access_token

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in Zerodha token data: {e}")
            self.delete_token()  # Clean up bad data
            return None
        except Exception as e:
            logger.error(f"Error retrieving Zerodha token: {e}", exc_info=True)
            return None

    def get_token_info(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve complete token information including metadata.

        Returns:
            dict: Token data with access_token, user_id, timestamps, profile
                  None if not found or invalid

        Example:
            >>> info = manager.get_token_info()
            >>> if info:
            ...     print(f"Token for {info['user_id']}, expires {info['expires_at']}")
        """
        try:
            # RedisCache.get() automatically deserializes JSON
            data = self.redis.get(self.REDIS_KEY)

            if not data:
                return None

            # data is already a dict (deserialized by RedisCache)

            # Validate required fields
            if not data.get("access_token") or not data.get("user_id"):
                logger.warning("Incomplete Zerodha token data in Redis")
                return None

            return data

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in Zerodha token data: {e}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving Zerodha token info: {e}", exc_info=True)
            return None

    def delete_token(self) -> bool:
        """
        Delete Zerodha token from Redis.

        Use when token is invalidated or needs to be refreshed.

        Returns:
            bool: True if deleted successfully

        Example:
            >>> manager.delete_token()  # Force re-authentication
            True
        """
        try:
            success = self.redis.delete(self.REDIS_KEY)
            if success:
                logger.info("Zerodha token deleted from Redis")
            return success
        except Exception as e:
            logger.error(f"Error deleting Zerodha token: {e}", exc_info=True)
            return False

    def is_token_valid(self) -> bool:
        """
        Check if a valid token exists in Redis.

        Returns:
            bool: True if valid token exists, False otherwise

        Example:
            >>> if not manager.is_token_valid():
            ...     # Need to re-authenticate
            ...     authenticate_zerodha()
        """
        return self.get_token() is not None

    def get_token_expiry(self) -> Optional[datetime]:
        """
        Get token expiration timestamp.

        Returns:
            datetime: Expiration time in UTC, None if no token

        Example:
            >>> expiry = manager.get_token_expiry()
            >>> if expiry:
            ...     time_left = expiry - datetime.utcnow()
            ...     print(f"Token expires in {time_left.total_seconds()/3600:.1f} hours")
        """
        info = self.get_token_info()
        if info and info.get("expires_at"):
            try:
                return datetime.fromisoformat(info["expires_at"])
            except:
                pass
        return None


# Singleton instance getter
_global_token_manager = None

def get_token_manager(redis_client=None):
    """
    Get or create global ZerodhaTokenManager instance.

    Args:
        redis_client: Optional RedisCache instance (required on first call)

    Returns:
        ZerodhaTokenManager: Singleton instance

    Example:
        >>> from app.cache.redis_client import get_redis_cache
        >>> redis = get_redis_cache()
        >>> manager = get_token_manager(redis)
        >>> manager.store_token("xyz123", "AB1234")
    """
    global _global_token_manager

    if _global_token_manager is None:
        if redis_client is None:
            raise ValueError("redis_client required for first initialization")
        _global_token_manager = ZerodhaTokenManager(redis_client)
        logger.info("ZerodhaTokenManager initialized")

    return _global_token_manager
