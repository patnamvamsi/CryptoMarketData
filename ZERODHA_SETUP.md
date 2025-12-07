# Zerodha/NSE Market Data Integration Setup Guide

This guide explains how to set up and use the Zerodha/NSE market data capture functionality in the CryptoMarketData application.

## Overview

The application now supports **automatic NSE market data capture** on startup, including:
- Real-time streaming of NSE stock data via Zerodha KiteTicker WebSocket
- Historical data fetching and gap filling
- Multi-symbol tracking with priority management
- Unified database schema for both Binance and Zerodha data

## Prerequisites

1. **Zerodha Kite API Access**
   - Active Zerodha trading account
   - Kite Connect API subscription (₹2000/month)
   - API key and secret from: https://developers.kite.trade/

2. **Dependencies**
   ```bash
   pip install kiteconnect
   ```

3. **Database**
   - TimescaleDB running and configured
   - Unified symbols table created (see database setup)

## Configuration

### 1. API Credentials

Update your `app/config/config.py` with Zerodha credentials:

```python
# Zerodha API credentials
ZERODHA_API_KEY = 'your_api_key_here'
ZERODHA_SECRET_KEY = 'your_secret_key_here'

# Zerodha settings
ZERODHA_EXCHANGE_SEGMENT = 'NSE'  # NSE, NFO, BSE
ZERODHA_DEFAULT_INTERVAL = '1m'   # 1m, 5m, 15m, 1h, 1d
ENABLE_ZERODHA_STREAMING = True   # Enable streaming on startup
ENABLE_ZERODHA_GAP_FILL = True    # Enable gap filling on startup
```

### 2. Default Symbols

Configure which NSE symbols to track by default in `app/config/config.py`:

```python
DEFAULT_NSE_SYMBOLS = [
    'RELIANCE',
    'TCS',
    'HDFCBANK',
    'INFY',
    'ICICIBANK',
    'HINDUNILVR',
    'SBIN',
    'BHARTIARTL',
    'ITC',
    'KOTAKBANK',
    'LT',
    'AXISBANK',
    'ASIANPAINT',
    'MARUTI',
    'WIPRO'
]
```

## Authentication

Zerodha requires **daily authentication** with an access token (valid for 24 hours).

### Option 1: Using zerodha_auth.py (Recommended for first-time setup)

1. Run the authentication script:
   ```bash
   python zerodha_auth.py
   ```

2. Follow the login flow in your browser

3. The script will save the access token to `zerodha_access_token.txt`

### Option 2: Manual Token Generation

1. Get the login URL:
   ```python
   from kiteconnect import KiteConnect
   kite = KiteConnect(api_key="your_api_key")
   print(kite.login_url())
   ```

2. Visit the URL, login, and copy the `request_token` from redirect URL

3. Generate access token:
   ```python
   data = kite.generate_session(request_token, api_secret="your_secret")
   access_token = data["access_token"]
   # Save to zerodha_access_token.txt
   ```

### Option 3: Automated Daily Token Refresh

For production, set up a cron job to refresh the token daily:

```bash
# Add to crontab (runs at 8:30 AM IST every day)
30 8 * * * cd /path/to/CryptoMarketData && python zerodha_auth.py
```

## Starting the Application

### Development Mode

```bash
cd app/
python -m uvicorn main:app --reload --port 8002
```

### What Happens on Startup

1. **Symbol Initialization**: If no active Zerodha symbols exist, the app automatically:
   - Refreshes symbols from Zerodha API
   - Activates default NSE symbols from config
   - Creates necessary database tables

2. **Background Schedulers Start**:
   - **Zerodha Streaming**: Connects to KiteTicker WebSocket for real-time data
   - **Gap Filling**: Identifies and fills missing data periods
   - **Binance Services**: Continues running as before (if enabled)

3. **Data Flow**:
   ```
   Zerodha API → ZerodhaAdapter → StreamZerodhaKLineData → TimescaleDB
                                                           ↓
                                                      Kafka (optional)
   ```

## API Endpoints

### Zerodha-Specific Endpoints

#### Refresh Symbols from API
```bash
GET /zerodha/update/symbols?exchange_segment=NSE
```

#### Activate/Deactivate Symbol
```bash
POST /zerodha/activatesymbol/{symbol}/{priority}/{state}

# Example: Activate RELIANCE with priority 1
POST /zerodha/activatesymbol/RELIANCE/1/true
```

#### Get Active Symbols
```bash
GET /zerodha/active-symbols
```

#### Initialize Default Symbols
```bash
POST /zerodha/initialize-defaults
```

#### Update Market Data for Symbol
```bash
GET /zerodha/update/marketdata/{symbol}?exchange_segment=NSE&interval=1m

# Example: Fetch 1-minute data for INFY
GET /zerodha/update/marketdata/INFY?exchange_segment=NSE&interval=1m
```

## Database Schema

### Symbols Table (Unified for All Exchanges)

```sql
SELECT * FROM symbols WHERE exchange = 'zerodha';
```

Columns:
- `exchange`: 'zerodha'
- `symbol`: Trading symbol (e.g., 'RELIANCE')
- `base_asset`: Company name
- `quote_asset`: 'INR'
- `status`: 'TRADING'
- `priority`: Lower = higher priority
- `active`: Boolean
- `metadata`: JSONB with instrument_token, segment, etc.

### Time-Series Tables

Data is stored in exchange-specific hypertables:

```
zerodha_{symbol}_kline_{interval}

Examples:
- zerodha_reliance_kline_1m
- zerodha_tcs_kline_5m
- zerodha_infy_kline_1h
```

## Programmatic Usage

### Using ZerodhaDownloader

```python
from app.ingest.zerodha_historical_data import ZerodhaDownloader
from app.db.timescaledb import timescaledb_connect as c

session_pool = c.get_session_pool()
session = session_pool()

# Fetch historical data
downloader = ZerodhaDownloader(session, exchange_segment='NSE', interval='5m')
downloader.fetch_recent_historical_data('RELIANCE')

# Fill gaps
downloader.fetch_all_gap_historical_data()

session.close()
```

### Using ZerodhaAdapter Directly

```python
from app.exchanges import get_exchange
from datetime import datetime, timedelta

# Initialize adapter (loads token from file)
zerodha = get_exchange('zerodha')

# Get symbols
nse_symbols = zerodha.get_symbols(exchange='NSE')

# Fetch historical data
klines = zerodha.get_historical_data(
    symbol='RELIANCE',
    interval='5m',
    start_time=datetime.now() - timedelta(days=1),
    end_time=datetime.now(),
    exchange='NSE'
)

# Start streaming
def handle_tick(tick_data):
    print(f"Received tick: {tick_data}")

zerodha.start_streaming(
    symbols=['RELIANCE', 'TCS', 'INFY'],
    callback=handle_tick,
    exchange='NSE'
)
```

## Troubleshooting

### Common Issues

1. **"Zerodha access token expired or invalid"**
   - Solution: Run `python zerodha_auth.py` to generate a fresh token

2. **"Symbol not found" errors**
   - Solution: Refresh symbols from API: `GET /zerodha/update/symbols`

3. **No data streaming**
   - Check if symbols are activated: `GET /zerodha/active-symbols`
   - Verify token is valid
   - Check logs for WebSocket connection errors

4. **Database table missing**
   - Tables are created automatically on first data fetch
   - Ensure unified symbols table exists (run database migration)

### Logging

All Zerodha operations are logged. Check logs for:
- Symbol initialization
- API calls
- WebSocket connection status
- Data ingestion

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Market Hours

NSE trading hours (IST):
- **Pre-market**: 9:00 AM - 9:15 AM
- **Regular trading**: 9:15 AM - 3:30 PM
- **Post-market**: 3:40 PM - 4:00 PM

Data streaming is only available during market hours. Historical data can be fetched anytime.

## Performance Considerations

1. **Symbol Limits**: Zerodha supports up to 3000 instruments per WebSocket connection
2. **Rate Limits**: Historical API has rate limits (3 requests/second)
3. **Data Volume**: 1-minute data for 15 symbols ≈ 6750 candles/day
4. **Database**: TimescaleDB handles compression and partitioning automatically

## Configuration Options

Disable Zerodha services without removing code:

```python
# In app/config/config.py
ENABLE_ZERODHA_STREAMING = False  # Disable streaming
ENABLE_ZERODHA_GAP_FILL = False   # Disable gap filling
```

## Next Steps

1. **Monitor the Application**:
   ```bash
   # Check logs
   tail -f logs/app.log

   # Check database
   psql -d market_data_dev1 -c "SELECT * FROM symbols WHERE exchange='zerodha';"
   ```

2. **Verify Data Ingestion**:
   ```sql
   -- Check recent data for RELIANCE
   SELECT * FROM zerodha_reliance_kline_1m
   ORDER BY open_time DESC
   LIMIT 10;
   ```

3. **Add Custom Symbols**:
   ```bash
   # Activate TATASTEEL with priority 3
   POST /zerodha/activatesymbol/TATASTEEL/3/true
   ```

## Support

For issues related to:
- **Kite API**: https://kite.trade/docs/connect/v3/
- **TimescaleDB**: https://docs.timescale.com/
- **Application**: Check logs and GitHub issues

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Startup Initialization                   │  │
│  │  1. Check active Zerodha symbols                     │  │
│  │  2. Initialize defaults if none exist                │  │
│  │  3. Start background schedulers                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────────────────┐   │
│  │ ZerodhaAdapter   │  │ StreamZerodhaKLineData       │   │
│  │ (Exchange Layer) │→ │ (Streaming Layer)            │   │
│  └──────────────────┘  └──────────────────────────────┘   │
│           ↓                        ↓                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Database Layer (CRUD operations)             │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓
              ┌──────────────────────────┐
              │   TimescaleDB            │
              │   - symbols (unified)    │
              │   - zerodha_*_kline_*    │
              └──────────────────────────┘
```
