"""
Zerodha Exchange Adapter

This module implements the BaseExchange interface for Zerodha/NSE stock exchange.
It wraps the KiteConnect API client and provides a unified interface for market data operations.
"""

from typing import List, Dict, Any, Callable, Optional
from datetime import datetime
import logging
import traceback
import pytz
import os

from kiteconnect import KiteConnect, KiteTicker

from .base_exchange import BaseExchange
from app.config import config
from app.models.market_data import Symbol, Kline

# Try to import automated auth modules. Browser-based (real headless Chromium, drives the
# actual login page) is tried first — see app/auth/zerodha_browser_auth.py for why. The
# older raw-requests approach is kept as a fallback in case Playwright is ever unavailable.
try:
    from app.auth.zerodha_browser_auth import (
        get_request_token as get_request_token_browser,
        ZerodhaBrowserAuthError,
    )
    BROWSER_AUTH_AVAILABLE = True
except ImportError as e:
    BROWSER_AUTH_AVAILABLE = False
    ZerodhaBrowserAuthError = Exception  # Fallback

try:
    from app.auth.zerodha_auto_auth import get_request_token, ZerodhaAuthError
    AUTO_AUTH_AVAILABLE = True
except ImportError as e:
    AUTO_AUTH_AVAILABLE = False
    ZerodhaAuthError = Exception  # Fallback

# Try to import token manager for Redis-based token sharing
try:
    from app.auth.token_manager import get_token_manager
    TOKEN_MANAGER_AVAILABLE = True
except ImportError as e:
    TOKEN_MANAGER_AVAILABLE = False

logger = logging.getLogger(__name__)
if not BROWSER_AUTH_AVAILABLE:
    logger.warning("Browser-based Zerodha authentication not available (missing dependencies)")
if not AUTO_AUTH_AVAILABLE:
    logger.warning("Automated Zerodha authentication not available (missing dependencies)")
if not TOKEN_MANAGER_AVAILABLE:
    logger.warning("Token manager not available (missing dependencies)")



class ZerodhaAdapter(BaseExchange):
    """
    Zerodha/NSE exchange adapter implementation.

    Provides access to Indian stock market data including:
    - Instrument/symbol information (NSE, NFO, BSE)
    - Historical candlestick data
    - Real-time streaming via WebSocket (KiteTicker)

    Note: Zerodha requires daily authentication with access token.
    """

    # File to store access token (relative to app directory)
    ACCESS_TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "zerodha_access_token.txt")

    def __init__(self, access_token: Optional[str] = None, request_token: Optional[str] = None):
        """
        Initialize Zerodha adapter with authentication.

        Authentication priority (first successful method used):
        1. Provided access_token parameter (explicit override)
        2. Provided request_token parameter (explicit override)
        3. Shared token from Redis (written by this adapter, store_zerodha_token_redis.py,
           or another service)
        4. Automated authentication using credentials from config
        5. Load from file (app/zerodha_access_token.txt)
        6. Raise exception if all methods fail

        Args:
            access_token: Pre-generated access token (valid for 1 day)
            request_token: Request token from login redirect (for generating access token)

        Raises:
            Exception: If all authentication methods fail
        """
        super().__init__()
        self._exchange_name = 'zerodha'

        # Initialize KiteConnect client
        try:
            self.kite = KiteConnect(api_key=config.ZERODHA_API_KEY)
            logger.info("KiteConnect client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize KiteConnect client: {e}")
            raise

        # Tier 1: Use provided access token
        if access_token:
            logger.debug("Using provided access_token parameter")
            self._set_and_validate_token(access_token)

        # Tier 2: Generate from provided request token
        elif request_token:
            logger.debug("Generating access_token from provided request_token parameter")
            access_token = self._generate_access_token(request_token)
            self._set_and_validate_token(access_token)

        else:
            auth_success = False

            # Tier 3: Try the shared Redis token (written by this adapter on a
            # previous successful auth, by scripts/store_zerodha_token_redis.py,
            # or by another service). Checked before auto-auth so a still-valid
            # shared token is reused instead of re-hitting Zerodha's login flow.
            if TOKEN_MANAGER_AVAILABLE:
                try:
                    from app.cache.redis_client import get_redis_cache
                    redis = get_redis_cache()
                    if redis and redis.enabled:
                        shared_token = get_token_manager(redis).get_token()
                        if shared_token:
                            try:
                                self._set_and_validate_token(shared_token)
                                auth_success = True
                                logger.info("Using shared access token from Redis")
                            except Exception as e:
                                logger.warning(f"Shared Redis token invalid: {e}")
                except Exception as e:
                    logger.warning(f"Failed to check shared Redis token: {e}")

            # Tier 4: Try automated authentication
            if not auth_success and self._should_attempt_auto_auth():
                logger.info("Attempting automated Zerodha authentication...")
                try:
                    auto_request_token = self._try_automated_auth()
                    if auto_request_token:
                        access_token = self._generate_access_token(auto_request_token)
                        self._set_and_validate_token(access_token)
                        auth_success = True
                        logger.info("Automated authentication successful!")
                except Exception as e:
                    logger.warning(f"Automated authentication failed: {e}")
                    logger.info("Falling back to manual authentication flow...")

            # Tier 5: Load from file (if Redis/auto-auth failed or were skipped)
            if not auth_success:
                loaded_token = self._load_access_token()
                if loaded_token:
                    try:
                        self._set_and_validate_token(loaded_token)
                        logger.info("Using access token from file")
                    except Exception as e:
                        logger.warning(f"Saved access token invalid: {e}")
                        raise Exception(
                            "Zerodha access token expired or invalid. "
                            "Please provide credentials for automated auth or manually generate token."
                        )
                else:
                    # Tier 6: No authentication available
                    raise Exception(
                        "No Zerodha authentication available. Please either:\n"
                        "1. Set ZERODHA_USERNAME, ZERODHA_PASSWORD, ZERODHA_TOTP_KEY in .env for automated auth\n"
                        "2. Provide access_token parameter\n"
                        "3. Provide request_token parameter\n"
                        "4. Run scripts/zerodha_auth.py to generate token manually"
                    )

        # WebSocket ticker (lazy initialized)
        self.kws: Optional[KiteTicker] = None
        self._stream_callback: Optional[Callable] = None

        # Instrument token cache: symbol -> token mapping
        self._instrument_cache: Dict[str, int] = {}
        self._token_to_symbol_cache: Dict[int, str] = {}

    @property
    def exchange_name(self) -> str:
        """Return 'zerodha' as the exchange identifier."""
        return self._exchange_name

    def get_symbols(self, exchange: str = "NSE") -> List[Dict[str, Any]]:
        """
        Fetch all available instruments from Zerodha.

        Args:
            exchange: Exchange segment (NSE, NFO, BSE, etc.)

        Returns:
            List of normalized symbol dictionaries

        Raises:
            Exception: If API call fails
        """
        try:
            # Fetch instruments for the specified exchange
            instruments_raw = self.kite.instruments(exchange)

            logger.info(f"Fetched {len(instruments_raw)} instruments from Zerodha {exchange}")

            # Normalize each instrument and update cache
            normalized_symbols = []
            for instrument_data in instruments_raw:
                normalized = self.normalize_symbol(instrument_data)
                normalized_symbols.append(normalized)

                # Update instrument token cache
                symbol = instrument_data['tradingsymbol']
                token = instrument_data['instrument_token']
                self._instrument_cache[f"{exchange}:{symbol}"] = token
                self._token_to_symbol_cache[token] = f"{exchange}:{symbol}"

            return normalized_symbols

        except Exception as e:
            logger.error(f"Error fetching symbols from Zerodha {exchange}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        exchange: str = "NSE"
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candlestick data for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'RELIANCE', 'INFY')
            interval: Time interval (e.g., '1m', '5m', '1h', '1d')
            start_time: Start datetime
            end_time: End datetime
            exchange: Exchange segment (default: 'NSE')

        Returns:
            List of normalized kline dictionaries

        Raises:
            Exception: If API call fails or symbol not found
        """
        try:
            # Get instrument token for the symbol
            instrument_token = self._get_instrument_token(symbol, exchange)

            # Convert interval to Zerodha format
            zerodha_interval = self._convert_interval(interval)

            # Fetch historical data
            candles = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=start_time,
                to_date=end_time,
                interval=zerodha_interval
            )

            logger.info(
                f"Fetched {len(candles)} candles for {exchange}:{symbol} "
                f"from {start_time} to {end_time}"
            )

            # Normalize each candle
            normalized_klines = [
                self.normalize_kline(candle, symbol, interval)
                for candle in candles
            ]

            return normalized_klines

        except Exception as e:
            logger.error(
                f"Error getting historical data for {exchange}:{symbol}: {e}"
            )
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    def start_streaming(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None],
        exchange: str = "NSE"
    ) -> None:
        """
        Start real-time WebSocket streaming for given symbols.

        Args:
            symbols: List of trading symbols
            callback: Function to call with each tick update
            exchange: Exchange segment (default: 'NSE')

        Raises:
            Exception: If streaming fails to start
        """
        try:
            # Get instrument tokens for symbols
            instrument_tokens = [
                self._get_instrument_token(symbol, exchange)
                for symbol in symbols
            ]

            logger.info(f"Starting Zerodha WebSocket for {len(symbols)} symbols")
            logger.debug(f"Instrument tokens: {instrument_tokens}")

            # Store callback for use in message handler
            self._stream_callback = callback

            # Initialize KiteTicker
            self.kws = KiteTicker(
                api_key=config.ZERODHA_API_KEY,
                access_token=self.kite.access_token
            )

            # Define WebSocket event handlers
            def on_ticks(ws, ticks):
                """Process incoming tick data."""
                try:
                    for tick in ticks:
                        # Normalize tick to kline format
                        normalized_kline = self._normalize_tick(tick)
                        # Call user callback
                        self._stream_callback(normalized_kline)
                except Exception as e:
                    logger.error(f"Error processing tick: {e}")
                    traceback.print_exception(type(e), e, e.__traceback__)

            def on_connect(ws, response):
                """Handle WebSocket connection."""
                logger.info("Connected to Zerodha WebSocket")
                # Subscribe to instruments with full mode
                ws.subscribe(instrument_tokens)
                ws.set_mode(ws.MODE_FULL, instrument_tokens)

            def on_close(ws, code, reason):
                """Handle WebSocket disconnection."""
                logger.warning(f"Zerodha WebSocket closed: {code} - {reason}")
                self._streaming_active = False

            def on_error(ws, code, reason):
                """Handle WebSocket errors."""
                logger.error(f"Zerodha WebSocket error: {code} - {reason}")

            # Set event handlers
            self.kws.on_ticks = on_ticks
            self.kws.on_connect = on_connect
            self.kws.on_close = on_close
            self.kws.on_error = on_error

            # Connect (threaded mode)
            self.kws.connect(threaded=True)
            self._streaming_active = True

            logger.info("Zerodha WebSocket streaming started successfully")

        except Exception as e:
            logger.error(f"Failed to start Zerodha streaming: {e}")
            self._streaming_active = False
            raise

    def stop_streaming(self) -> None:
        """Stop active WebSocket streaming connection."""
        try:
            if self.kws:
                logger.info("Stopping Zerodha WebSocket streaming")
                self.kws.close()
                self.kws = None
                self._streaming_active = False
                self._stream_callback = None
                logger.info("Zerodha WebSocket streaming stopped")
            else:
                logger.warning("No active streaming to stop")

        except Exception as e:
            logger.error(f"Error stopping Zerodha streaming: {e}")
            raise

    def normalize_symbol(self, raw_instrument: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Zerodha instrument format to unified format.

        Args:
            raw_instrument: Raw instrument data from Zerodha API

        Returns:
            Normalized symbol dictionary

        Example Zerodha instrument:
            {
                'instrument_token': 408065,
                'exchange_token': 1594,
                'tradingsymbol': 'INFY',
                'name': 'INFOSYS LIMITED',
                'last_price': 0.0,
                'expiry': '',
                'strike': 0.0,
                'tick_size': 0.05,
                'lot_size': 1,
                'instrument_type': 'EQ',
                'segment': 'NSE',
                'exchange': 'NSE'
            }
        """
        # Convert expiry date to ISO string for JSON serialization
        expiry = raw_instrument.get('expiry')
        if expiry and hasattr(expiry, 'isoformat'):
            expiry = expiry.isoformat()
        elif expiry == '':
            expiry = None

        return {
            'exchange': 'zerodha',
            'symbol': raw_instrument['tradingsymbol'],
            'base_asset': raw_instrument.get('name', raw_instrument['tradingsymbol']),
            'quote_asset': 'INR',  # NSE stocks are quoted in INR
            'status': 'TRADING',  # Zerodha doesn't provide status
            'priority': 9999,  # Default low priority
            'active': False,   # Default inactive
            'metadata': {
                'instrument_token': raw_instrument['instrument_token'],
                'exchange_token': raw_instrument['exchange_token'],
                'segment': raw_instrument['segment'],
                'exchange_raw': raw_instrument['exchange'],
                'instrument_type': raw_instrument.get('instrument_type'),
                'tick_size': raw_instrument.get('tick_size'),
                'lot_size': raw_instrument.get('lot_size'),
                'expiry': expiry,
                'strike': raw_instrument.get('strike')
            }
        }

    def normalize_kline(
        self,
        raw_candle: Any,
        symbol: str,
        interval: str
    ) -> Dict[str, Any]:
        """
        Convert Zerodha candle format to unified format.

        Args:
            raw_candle: Raw candle data from Zerodha (dict format)
            symbol: Trading symbol
            interval: Time interval

        Returns:
            Normalized kline dictionary

        Zerodha candle format:
            {
                'date': datetime.datetime(2023, 1, 1, 9, 15),
                'open': 2500.0,
                'high': 2550.0,
                'low': 2490.0,
                'close': 2520.0,
                'volume': 1000
            }
        """
        # Ensure timezone-aware datetime
        candle_time = raw_candle['date']
        if candle_time.tzinfo is None:
            candle_time = pytz.timezone('Asia/Kolkata').localize(candle_time)

        return {
            'exchange': 'zerodha',
            'symbol': symbol.upper(),
            'interval': interval,
            'open_time': candle_time,
            'close_time': candle_time,  # Zerodha doesn't provide close time
            'open': float(raw_candle['open']),
            'high': float(raw_candle['high']),
            'low': float(raw_candle['low']),
            'close': float(raw_candle['close']),
            'volume': float(raw_candle['volume']),
            'quote_volume': None,  # Not provided by Zerodha
            'trades': None,  # Not provided by Zerodha
            'taker_buy_base_volume': None,
            'taker_buy_quote_volume': None,
            'extra_data': {}
        }

    def _normalize_tick(self, tick: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize WebSocket tick to unified kline format.

        Args:
            tick: Tick data from KiteTicker

        Returns:
            Normalized kline dictionary

        Tick format:
            {
                'instrument_token': 408065,
                'last_price': 1500.0,
                'ohlc': {
                    'open': 1480.0,
                    'high': 1510.0,
                    'low': 1475.0,
                    'close': 1495.0
                },
                'volume': 50000,
                'timestamp': datetime.datetime(...)
            }
        """
        # Get symbol from token
        token = tick['instrument_token']
        symbol = self._token_to_symbol_cache.get(token, f"TOKEN_{token}")

        # Get timestamp (use current time if not provided)
        tick_time = tick.get('timestamp', tick.get('exchange_timestamp', datetime.now()))
        if tick_time.tzinfo is None:
            tick_time = pytz.timezone('Asia/Kolkata').localize(tick_time)

        ohlc = tick.get('ohlc', {})

        return {
            'exchange': 'zerodha',
            'symbol': symbol.split(':')[-1] if ':' in symbol else symbol,
            'interval': '1m',  # Tick data
            'open_time': tick_time,
            'close_time': tick_time,
            'open': float(ohlc.get('open', tick.get('last_price', 0))),
            'high': float(ohlc.get('high', tick.get('last_price', 0))),
            'low': float(ohlc.get('low', tick.get('last_price', 0))),
            'close': float(tick.get('last_price', 0)),
            'volume': float(tick.get('volume', 0)),
            'quote_volume': None,
            'trades': None,
            'taker_buy_base_volume': None,
            'taker_buy_quote_volume': None,
            'extra_data': {
                'instrument_token': token,
                'mode': tick.get('mode'),
                'tradable': tick.get('tradable'),
                'change': tick.get('change')
            }
        }

    def _convert_interval(self, interval: str) -> str:
        """
        Convert unified interval format to Zerodha format.

        Args:
            interval: Unified interval (e.g., '1m', '5m', '1h')

        Returns:
            Zerodha interval string
        """
        interval_map = {
            '1m': 'minute',
            '3m': '3minute',
            '5m': '5minute',
            '10m': '10minute',
            '15m': '15minute',
            '30m': '30minute',
            '1h': '60minute',
            '1d': 'day'
        }

        return interval_map.get(interval, 'minute')

    def validate_interval(self, interval: str) -> bool:
        """
        Validate if interval is supported by Zerodha.

        Args:
            interval: Time interval to validate

        Returns:
            True if supported, False otherwise
        """
        supported = ['1m', '3m', '5m', '10m', '15m', '30m', '1h', '1d']
        return interval in supported

    def _get_instrument_token(self, symbol: str, exchange: str = "NSE") -> int:
        """
        Get instrument token for a symbol.

        Args:
            symbol: Trading symbol
            exchange: Exchange segment

        Returns:
            Instrument token (integer)

        Raises:
            ValueError: If symbol not found
        """
        cache_key = f"{exchange}:{symbol}"

        # Check cache first
        if cache_key in self._instrument_cache:
            return self._instrument_cache[cache_key]

        # Fetch from API if not in cache
        try:
            instruments = self.kite.instruments(exchange)
            for inst in instruments:
                if inst['tradingsymbol'] == symbol:
                    token = inst['instrument_token']
                    # Update cache
                    self._instrument_cache[cache_key] = token
                    self._token_to_symbol_cache[token] = cache_key
                    return token

            raise ValueError(f"Symbol {exchange}:{symbol} not found")

        except Exception as e:
            logger.error(f"Error fetching instrument token for {cache_key}: {e}")
            raise

    def _generate_access_token(self, request_token: str) -> str:
        """
        Generate access token from request token.

        Args:
            request_token: Request token from login redirect

        Returns:
            Access token string

        Raises:
            Exception: If token generation fails
        """
        try:
            data = self.kite.generate_session(
                request_token,
                api_secret=config.ZERODHA_SECRET_KEY
            )
            access_token = data["access_token"]

            # Save to file
            self._save_access_token(access_token)

            logger.info("Access token generated and saved")
            return access_token

        except Exception as e:
            logger.error(f"Failed to generate access token: {e}")
            raise

    def _set_and_validate_token(self, access_token: str) -> None:
        """
        Set and validate access token.

        Also stores token in Redis for sharing with other microservices.

        Args:
            access_token: Access token to set

        Raises:
            Exception: If token is invalid
        """
        #data = self.kite.generate_session(access_token, api_secret=config.ZERODHA_SECRET_KEY)
        self.kite.set_access_token(access_token)

        # Validate by fetching profile
        try:
            profile = self.kite.profile()
            user_id = profile.get('user_id', profile.get('user_name', 'Unknown'))
            logger.info(f"Authenticated as: {profile.get('user_name', 'Unknown')}")

            # Store token in Redis for sharing with other microservices (webserver, etc.)
            if TOKEN_MANAGER_AVAILABLE:
                try:
                    from app.cache.redis_client import get_redis_cache
                    redis = get_redis_cache()
                    if redis and redis.enabled:
                        token_manager = get_token_manager(redis)
                        token_manager.store_token(access_token, user_id, profile)
                        logger.info(f"Zerodha token stored in Redis for cross-service access")
                    else:
                        logger.warning("Redis not available, token not stored for sharing")
                except Exception as e:
                    logger.warning(f"Failed to store token in Redis: {e}")
                    # Non-critical, continue anyway

        except Exception as e:
            logger.error(f"Access token validation failed: {e}")
            raise

    def _save_access_token(self, token: str) -> None:
        """Save access token to file."""
        try:
            with open(self.ACCESS_TOKEN_FILE, "w") as f:
                f.write(token)
            logger.debug("Access token saved to file")
        except Exception as e:
            logger.warning(f"Failed to save access token: {e}")

    def _load_access_token(self) -> Optional[str]:
        """Load access token from file."""
        try:
            if os.path.exists(self.ACCESS_TOKEN_FILE):
                with open(self.ACCESS_TOKEN_FILE, "r") as f:
                    token = f.read().strip()
                logger.debug("Access token loaded from file")
                return token
        except Exception as e:
            logger.warning(f"Failed to load access token: {e}")

        return None

    def _should_attempt_auto_auth(self) -> bool:
        """
        Check if automated authentication should be attempted.

        Returns:
            bool: True if all conditions are met for auto-auth
        """
        if not BROWSER_AUTH_AVAILABLE and not AUTO_AUTH_AVAILABLE:
            logger.debug("No auto-auth module available")
            return False

        if not config.ENABLE_ZERODHA_AUTO_AUTH:
            logger.debug("Auto-auth disabled in config")
            return False

        # Check if all required credentials are provided
        has_credentials = all([
            config.ZERODHA_API_KEY,
            config.ZERODHA_USERNAME,
            config.ZERODHA_PASSWORD,
            config.ZERODHA_TOTP_KEY
        ])

        if not has_credentials:
            logger.debug("Auto-auth credentials not fully configured")
            return False

        return True

    def _try_automated_auth(self) -> Optional[str]:
        """
        Attempt automated authentication using credentials from config.

        Tries the real-browser (Playwright) approach first — it drives the actual login
        page, same as a human does manually, rather than forging requests to Zerodha's
        internal API endpoints. Falls back to the raw-requests approach only if Playwright
        is unavailable; that approach is known-broken (persistent 403 as of 2026-08-22, see
        docs/ZERODHA_TOKEN_SHARING.md) but kept as a fallback in case that ever changes.

        Returns:
            Optional[str]: Request token if successful, None otherwise

        Raises:
            ZerodhaAuthError: If authentication fails with detailed error
        """
        if BROWSER_AUTH_AVAILABLE:
            try:
                request_token = get_request_token_browser(
                    api_key=config.ZERODHA_API_KEY,
                    username=config.ZERODHA_USERNAME,
                    password=config.ZERODHA_PASSWORD,
                    totp_key=config.ZERODHA_TOTP_KEY,
                    timeout=30
                )
                logger.info("Browser-based automated authentication successful")
                return request_token
            except ZerodhaBrowserAuthError as e:
                logger.warning(f"Browser-based automated authentication failed: {e}")
                if not AUTO_AUTH_AVAILABLE:
                    raise ZerodhaAuthError(str(e)) from e
                logger.info("Falling back to raw-requests automated authentication...")

        try:
            request_token = get_request_token(
                api_key=config.ZERODHA_API_KEY,
                username=config.ZERODHA_USERNAME,
                password=config.ZERODHA_PASSWORD,
                totp_key=config.ZERODHA_TOTP_KEY,
                timeout=30
            )
            logger.info("Automated authentication successful")
            return request_token

        except ZerodhaAuthError as e:
            logger.error(f"Automated authentication failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in automated authentication: {e}")
            raise ZerodhaAuthError(f"Automated auth failed: {e}") from e

    def get_login_url(self) -> str:
        """
        Get the login URL for manual authentication.

        Returns:
            Login URL string
        """
        return self.kite.login_url()
