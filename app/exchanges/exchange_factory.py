"""
Exchange Factory

This module provides a factory pattern implementation for creating and managing
exchange adapter instances. It ensures only one instance per exchange is created
(singleton pattern per exchange).
"""

from typing import Dict, List, Optional, Any
from .base_exchange import BaseExchange
import logging

logger = logging.getLogger(__name__)


class ExchangeFactory:
    """
    Factory class for creating and managing exchange adapters.

    Uses a registry pattern to map exchange names to their adapter classes,
    and maintains a singleton instance per exchange.
    """

    # Class-level storage for adapter instances (singleton per exchange)
    _adapters: Dict[str, BaseExchange] = {}

    # Registry mapping exchange names to adapter classes
    _registry: Dict[str, type] = {}

    @classmethod
    def register_exchange(cls, exchange_name: str, adapter_class: type) -> None:
        """
        Register an exchange adapter class.

        This should be called during application initialization to register
        all available exchange adapters.

        Args:
            exchange_name: Lowercase exchange identifier (e.g., 'binance', 'zerodha')
            adapter_class: The adapter class that implements BaseExchange

        Raises:
            ValueError: If exchange_name is already registered
            TypeError: If adapter_class doesn't inherit from BaseExchange

        Example:
            >>> ExchangeFactory.register_exchange('binance', BinanceAdapter)
            >>> ExchangeFactory.register_exchange('zerodha', ZerodhaAdapter)
        """
        exchange_name = exchange_name.lower()

        if exchange_name in cls._registry:
            raise ValueError(f"Exchange '{exchange_name}' is already registered")

        if not issubclass(adapter_class, BaseExchange):
            raise TypeError(
                f"Adapter class must inherit from BaseExchange, "
                f"got {adapter_class.__name__}"
            )

        cls._registry[exchange_name] = adapter_class
        logger.info(f"Registered exchange adapter: {exchange_name} -> {adapter_class.__name__}")

    @classmethod
    def get_exchange(cls, exchange_name: str, **kwargs) -> BaseExchange:
        """
        Get or create an exchange adapter instance.

        Returns an existing instance if available, otherwise creates a new one.
        Additional keyword arguments are passed to the adapter constructor.

        Args:
            exchange_name: Lowercase exchange identifier (e.g., 'binance', 'zerodha')
            **kwargs: Additional arguments passed to adapter constructor
                     (e.g., access_token for Zerodha)

        Returns:
            BaseExchange: The exchange adapter instance

        Raises:
            ValueError: If exchange is not supported/registered
            Exception: If adapter initialization fails

        Example:
            >>> binance = ExchangeFactory.get_exchange('binance')
            >>> zerodha = ExchangeFactory.get_exchange('zerodha', access_token='abc123')
        """
        exchange_name = exchange_name.lower()

        # Check if exchange is registered
        if exchange_name not in cls._registry:
            supported = cls.get_supported_exchanges()
            raise ValueError(
                f"Exchange '{exchange_name}' is not supported. "
                f"Supported exchanges: {', '.join(supported)}"
            )

        # Return existing instance if available and no kwargs provided
        # If kwargs are provided, we need to create a new instance
        if exchange_name in cls._adapters and not kwargs:
            logger.debug(f"Returning cached instance for exchange: {exchange_name}")
            return cls._adapters[exchange_name]

        # Create new instance
        try:
            adapter_class = cls._registry[exchange_name]
            adapter_instance = adapter_class(**kwargs)

            # Cache the instance only if no kwargs (default instance)
            if not kwargs:
                cls._adapters[exchange_name] = adapter_instance
                logger.info(f"Created and cached new adapter instance for: {exchange_name}")
            else:
                logger.info(f"Created new adapter instance with custom params for: {exchange_name}")

            return adapter_instance

        except Exception as e:
            logger.error(f"Failed to create adapter for {exchange_name}: {e}")
            raise

    @classmethod
    def get_supported_exchanges(cls) -> List[str]:
        """
        Get list of all registered/supported exchanges.

        Returns:
            List[str]: List of exchange names (lowercase)

        Example:
            >>> ExchangeFactory.get_supported_exchanges()
            ['binance', 'zerodha']
        """
        return sorted(cls._registry.keys())

    @classmethod
    def is_exchange_supported(cls, exchange_name: str) -> bool:
        """
        Check if an exchange is supported.

        Args:
            exchange_name: Exchange identifier to check

        Returns:
            bool: True if supported, False otherwise

        Example:
            >>> ExchangeFactory.is_exchange_supported('binance')
            True
            >>> ExchangeFactory.is_exchange_supported('unknown')
            False
        """
        return exchange_name.lower() in cls._registry

    @classmethod
    def clear_cache(cls, exchange_name: Optional[str] = None) -> None:
        """
        Clear cached adapter instances.

        Useful for testing or when you need to reinitialize adapters
        (e.g., after updating credentials).

        Args:
            exchange_name: Specific exchange to clear, or None to clear all

        Example:
            >>> ExchangeFactory.clear_cache('binance')  # Clear only Binance
            >>> ExchangeFactory.clear_cache()  # Clear all
        """
        if exchange_name:
            exchange_name = exchange_name.lower()
            if exchange_name in cls._adapters:
                # Clean up streaming if active
                adapter = cls._adapters[exchange_name]
                if adapter.is_streaming_active():
                    adapter.stop_streaming()

                del cls._adapters[exchange_name]
                logger.info(f"Cleared cached adapter for: {exchange_name}")
        else:
            # Clear all adapters
            for adapter in cls._adapters.values():
                if adapter.is_streaming_active():
                    adapter.stop_streaming()

            cls._adapters.clear()
            logger.info("Cleared all cached adapters")

    @classmethod
    def get_all_active_exchanges(cls) -> List[BaseExchange]:
        """
        Get all currently active (cached) exchange adapter instances.

        Returns:
            List[BaseExchange]: List of active adapter instances

        Example:
            >>> active = ExchangeFactory.get_all_active_exchanges()
            >>> for adapter in active:
            ...     print(f"{adapter.exchange_name}: streaming={adapter.is_streaming_active()}")
        """
        return list(cls._adapters.values())

    @classmethod
    def get_exchange_info(cls) -> Dict[str, Any]:
        """
        Get information about all registered exchanges.

        Returns:
            Dict mapping exchange names to their info:
                {
                    'exchange_name': {
                        'registered': bool,
                        'cached': bool,
                        'streaming': bool (if cached)
                    }
                }

        Example:
            >>> info = ExchangeFactory.get_exchange_info()
            >>> print(info)
            {
                'binance': {'registered': True, 'cached': True, 'streaming': False},
                'zerodha': {'registered': True, 'cached': False}
            }
        """
        info = {}

        for exchange_name in cls._registry.keys():
            exchange_info = {
                'registered': True,
                'cached': exchange_name in cls._adapters
            }

            if exchange_name in cls._adapters:
                adapter = cls._adapters[exchange_name]
                exchange_info['streaming'] = adapter.is_streaming_active()

            info[exchange_name] = exchange_info

        return info


# Convenience function for common use case
def get_exchange(exchange_name: str, **kwargs) -> BaseExchange:
    """
    Convenience function to get an exchange adapter.

    This is a shorthand for ExchangeFactory.get_exchange().

    Args:
        exchange_name: Exchange identifier
        **kwargs: Additional arguments for adapter initialization

    Returns:
        BaseExchange: Exchange adapter instance

    Example:
        >>> from app.exchanges.exchange_factory import get_exchange
        >>> binance = get_exchange('binance')
    """
    return ExchangeFactory.get_exchange(exchange_name, **kwargs)
