"""
Unified Market Data Models

This module defines Pydantic models for market data that work across all exchanges.
These models ensure consistent data structures regardless of the underlying exchange.
"""

from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum


class SymbolStatus(str, Enum):
    """Trading status of a symbol"""
    TRADING = "TRADING"
    HALTED = "HALTED"
    BREAK = "BREAK"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"


class Symbol(BaseModel):
    """
    Unified symbol/instrument model for all exchanges.

    Attributes:
        exchange: Exchange identifier (e.g., 'binance', 'zerodha')
        symbol: Trading symbol/instrument name (e.g., 'BTCUSDT', 'RELIANCE')
        base_asset: Base asset/stock name (e.g., 'BTC', 'RELIANCE')
        quote_asset: Quote asset/currency (e.g., 'USDT', 'INR')
        status: Current trading status
        priority: Data collection priority (0=disabled, higher=more important)
        active: Whether this symbol is actively being tracked
        metadata: Exchange-specific additional data (stored as JSONB in DB)
        last_updated: Last time symbol metadata was updated
    """
    exchange: str = Field(..., description="Exchange name (lowercase)")
    symbol: str = Field(..., description="Trading symbol")
    base_asset: str = Field(..., description="Base asset name")
    quote_asset: str = Field(..., description="Quote asset/currency")
    status: str = Field(default="TRADING", description="Trading status")
    priority: int = Field(default=0, ge=0, le=10, description="Priority level 0-10")
    active: bool = Field(default=False, description="Whether symbol is actively tracked")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Exchange-specific metadata")
    last_updated: datetime = Field(default_factory=datetime.now, description="Last update timestamp")

    @validator('exchange')
    def exchange_lowercase(cls, v):
        """Ensure exchange name is lowercase"""
        return v.lower()

    @validator('symbol')
    def symbol_uppercase(cls, v):
        """Ensure symbol is uppercase for consistency"""
        return v.upper()

    class Config:
        json_schema_extra = {
            "example": {
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "status": "TRADING",
                "priority": 5,
                "active": True,
                "metadata": {
                    "baseAssetPrecision": 8,
                    "quotePrecision": 8
                },
                "last_updated": "2024-01-01T00:00:00"
            }
        }


class Kline(BaseModel):
    """
    Unified candlestick/kline model for all exchanges.

    Represents OHLCV (Open, High, Low, Close, Volume) data for a time period.

    Attributes:
        exchange: Exchange identifier
        symbol: Trading symbol
        interval: Time interval (e.g., '1m', '5m', '1h', '1d')
        open_time: Candle opening timestamp
        close_time: Candle closing timestamp
        open: Opening price
        high: Highest price in interval
        low: Lowest price in interval
        close: Closing price
        volume: Trading volume in base asset
        quote_volume: Trading volume in quote asset (optional)
        trades: Number of trades (optional)
        taker_buy_base_volume: Taker buy volume in base asset (optional)
        taker_buy_quote_volume: Taker buy volume in quote asset (optional)
        extra_data: Exchange-specific additional fields
    """
    exchange: str = Field(..., description="Exchange name (lowercase)")
    symbol: str = Field(..., description="Trading symbol")
    interval: str = Field(..., description="Time interval (e.g., '1m', '5m', '1h')")
    open_time: datetime = Field(..., description="Candle open time")
    close_time: datetime = Field(..., description="Candle close time")
    open: float = Field(..., description="Opening price", gt=0)
    high: float = Field(..., description="Highest price", gt=0)
    low: float = Field(..., description="Lowest price", gt=0)
    close: float = Field(..., description="Closing price", gt=0)
    volume: float = Field(..., description="Volume in base asset", ge=0)
    quote_volume: Optional[float] = Field(None, description="Volume in quote asset", ge=0)
    trades: Optional[int] = Field(None, description="Number of trades", ge=0)
    taker_buy_base_volume: Optional[float] = Field(None, description="Taker buy base volume", ge=0)
    taker_buy_quote_volume: Optional[float] = Field(None, description="Taker buy quote volume", ge=0)
    extra_data: Dict[str, Any] = Field(default_factory=dict, description="Exchange-specific data")

    @validator('high')
    def high_must_be_highest(cls, v, values):
        """Validate high is >= open, low, close"""
        if 'low' in values and v < values['low']:
            raise ValueError('high must be >= low')
        if 'open' in values and v < values['open']:
            raise ValueError('high must be >= open')
        return v

    @validator('low')
    def low_must_be_lowest(cls, v, values):
        """Validate low is <= open, close"""
        if 'open' in values and v > values['open']:
            raise ValueError('low must be <= open')
        return v

    @validator('close_time')
    def close_after_open(cls, v, values):
        """Validate close_time is after open_time"""
        if 'open_time' in values and v < values['open_time']:
            raise ValueError('close_time must be after open_time')
        return v

    @validator('exchange')
    def exchange_lowercase(cls, v):
        """Ensure exchange name is lowercase"""
        return v.lower()

    @validator('symbol')
    def symbol_uppercase(cls, v):
        """Ensure symbol is uppercase"""
        return v.upper()

    def to_db_row(self) -> Dict[str, Any]:
        """
        Convert to database row format for insertion.

        Returns:
            Dict with keys matching database column names
        """
        return {
            'open_time': self.open_time,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'close_time': self.close_time,
            'quote_asset_volume': self.quote_volume,
            'trades': self.trades,
            'taker_buy_base_asset_volume': self.taker_buy_base_volume,
            'taker_buy_quote_asset_volume': self.taker_buy_quote_volume,
            'ignore': 0  # Placeholder for compatibility
        }

    class Config:
        json_schema_extra = {
            "example": {
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "interval": "1m",
                "open_time": "2024-01-01T00:00:00",
                "close_time": "2024-01-01T00:01:00",
                "open": 42000.50,
                "high": 42100.00,
                "low": 41950.00,
                "close": 42050.25,
                "volume": 125.5,
                "quote_volume": 5268750.0,
                "trades": 1523,
                "extra_data": {}
            }
        }


class DataGap(BaseModel):
    """
    Represents a gap in historical data that needs to be filled.

    Attributes:
        exchange: Exchange identifier
        symbol: Trading symbol
        interval: Time interval
        gap_start: Start of the data gap
        gap_end: End of the data gap
        gap_duration_minutes: Duration of gap in minutes
    """
    exchange: str
    symbol: str
    interval: str
    gap_start: datetime
    gap_end: datetime
    gap_duration_minutes: int

    class Config:
        json_schema_extra = {
            "example": {
                "exchange": "binance",
                "symbol": "BTCUSDT",
                "interval": "1m",
                "gap_start": "2024-01-01T10:00:00",
                "gap_end": "2024-01-01T10:15:00",
                "gap_duration_minutes": 15
            }
        }


class StreamingStatus(BaseModel):
    """
    Status of streaming connection for an exchange.

    Attributes:
        exchange: Exchange identifier
        active: Whether streaming is active
        symbols_count: Number of symbols being streamed
        symbols: List of symbols being streamed
        started_at: When streaming started
        last_message_at: Timestamp of last received message
        errors: Any recent errors
    """
    exchange: str
    active: bool
    symbols_count: int
    symbols: List[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    errors: List[str] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "exchange": "binance",
                "active": True,
                "symbols_count": 10,
                "symbols": ["BTCUSDT", "ETHUSDT"],
                "started_at": "2024-01-01T00:00:00",
                "last_message_at": "2024-01-01T12:30:45",
                "errors": []
            }
        }


class ExchangeInfo(BaseModel):
    """
    Basic information about an exchange.

    Attributes:
        name: Exchange identifier
        display_name: Human-readable exchange name
        supported: Whether this exchange is implemented
        requires_auth: Whether authentication is required
        supports_streaming: Whether real-time streaming is supported
        supported_intervals: List of supported time intervals
    """
    name: str
    display_name: str
    supported: bool
    requires_auth: bool
    supports_streaming: bool
    supported_intervals: List[str]

    class Config:
        json_schema_extra = {
            "example": {
                "name": "binance",
                "display_name": "Binance",
                "supported": True,
                "requires_auth": True,
                "supports_streaming": True,
                "supported_intervals": ["1m", "5m", "15m", "1h", "1d"]
            }
        }
