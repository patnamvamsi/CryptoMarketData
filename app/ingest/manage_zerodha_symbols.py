"""
Zerodha Symbol Management

Manages NSE/BSE symbols in the unified symbols table.
Handles symbol refresh, activation, and priority management.
"""

import logging
from typing import List, Dict, Any

from app.exchanges import get_exchange
from app.exchanges.zerodha_adapter import ZerodhaAdapter
from app.config import config
import app.db.timescaledb.crud as q
from app.logger import setup_logging
from app.cache.decorators import cache_result

logger = setup_logging()


@cache_result(
    key_pattern="cryptomarket:exchange_info:zerodha:{exchange_segment}",
    ttl=3600,  # 1 hour
    enabled_check=lambda: config.ENABLE_REDIS_CACHE
)
def refresh_zerodha_symbols(session, exchange_segment='NSE'):
    """
    Refresh symbols from Zerodha API and upsert into unified symbols table.

    Args:
        session: Database session
        exchange_segment: Market segment (NSE, NFO, BSE)

    Returns:
        Number of symbols refreshed
    """
    try:
        logger.info(f"Refreshing Zerodha {exchange_segment} symbols from API...")

        # Initialize Zerodha adapter
        zerodha: ZerodhaAdapter = get_exchange('zerodha')

        # Fetch symbols from Zerodha
        symbols = zerodha.get_symbols(exchange=exchange_segment)

        logger.info(f"Fetched {len(symbols)} symbols from Zerodha {exchange_segment}")

        # Upsert each symbol into database
        count = 0
        for symbol_data in symbols:
            try:
                q.upsert_symbol(symbol_data, session)
                count += 1
            except Exception as e:
                logger.error(f"Failed to upsert symbol {symbol_data.get('symbol')}: {e}")
                continue

        session.commit()
        logger.info(f"Successfully refreshed {count} Zerodha symbols")

        return count

    except Exception as e:
        logger.error(f"Error refreshing Zerodha symbols: {e}")
        session.rollback()
        raise


def set_symbol_priority(
    symbol: str,
    priority: int,
    session,
    active: bool = True,
    exchange='zerodha'
):
    """
    Set priority and activation status for a symbol.

    Args:
        symbol: Trading symbol (e.g., 'RELIANCE', 'INFY')
        priority: Priority value (lower = higher priority)
        session: Database session
        active: Whether symbol should be active
        exchange: Exchange name (default: 'zerodha')
    """
    try:
        logger.info(f"Setting {exchange}:{symbol} - priority={priority}, active={active}")

        q.update_symbol_status(
            exchange=exchange,
            symbol=symbol,
            priority=priority,
            active=active,
            session=session
        )

        session.commit()
        logger.info(f"Successfully updated {exchange}:{symbol}")

    except Exception as e:
        logger.error(f"Error updating symbol {symbol}: {e}")
        session.rollback()
        raise


def activate_symbols(symbols: List[str], session, priority: int = 5, exchange='zerodha'):
    """
    Activate multiple symbols at once.

    Args:
        symbols: List of symbol strings
        session: Database session
        priority: Default priority for all symbols
        exchange: Exchange name (default: 'zerodha')
    """
    logger.info(f"Activating {len(symbols)} symbols for {exchange}")

    for symbol in symbols:
        try:
            set_symbol_priority(
                symbol=symbol,
                priority=priority,
                session=session,
                active=True,
                exchange=exchange
            )
        except Exception as e:
            logger.error(f"Failed to activate {symbol}: {e}")
            continue


def deactivate_symbols(symbols: List[str], session, exchange='zerodha'):
    """
    Deactivate multiple symbols at once.

    Args:
        symbols: List of symbol strings
        session: Database session
        exchange: Exchange name (default: 'zerodha')
    """
    logger.info(f"Deactivating {len(symbols)} symbols for {exchange}")

    for symbol in symbols:
        try:
            q.update_symbol_status(
                exchange=exchange,
                symbol=symbol,
                priority=9999,  # Low priority
                active=False,
                session=session
            )
        except Exception as e:
            logger.error(f"Failed to deactivate {symbol}: {e}")
            continue

    session.commit()


def get_active_symbols(session, exchange='zerodha') -> List[str]:
    """
    Get list of active symbols.

    Args:
        session: Database session
        exchange: Exchange name (default: 'zerodha')

    Returns:
        List of active symbol strings
    """
    try:
        symbols = q.get_active_symbols_unified(
            session=session,
            exchange=exchange,
            active=True
        )
        return list(symbols)
    except Exception as e:
        logger.error(f"Error fetching active symbols: {e}")
        return []


def initialize_default_nse_symbols(session, symbols: List[str] = None):
    """
    Initialize default NSE symbols for tracking.

    Args:
        session: Database session
        symbols: List of symbols to activate (default: from config.DEFAULT_NSE_SYMBOLS)
    """
    if symbols is None:
        # Get default symbols from config
        symbols = config.DEFAULT_NSE_SYMBOLS

    logger.info(f"Initializing {len(symbols)} default NSE symbols: {', '.join(symbols)}")

    # First, refresh symbols from API to ensure they exist
    try:
        refresh_zerodha_symbols(session, exchange_segment='NSE')
    except Exception as e:
        logger.error(f"Failed to refresh symbols from API: {e}")
        logger.warning("Continuing with activation anyway...")

    # Activate the symbols
    activate_symbols(symbols, session, priority=5, exchange='zerodha')

    logger.info("Default NSE symbols initialized successfully")


if __name__ == "__main__":
    # Test symbol management
    from app.db.timescaledb import timescaledb_connect as c

    session_pool = c.get_session_pool()
    session = session_pool()

    try:
        # Refresh symbols from API
        count = refresh_zerodha_symbols(session, exchange_segment='NSE')
        print(f"Refreshed {count} symbols")

        # Initialize default symbols
        initialize_default_nse_symbols(session)

        # Get active symbols
        active = get_active_symbols(session, exchange='zerodha')
        print(f"Active symbols: {active}")

    finally:
        session.close()
