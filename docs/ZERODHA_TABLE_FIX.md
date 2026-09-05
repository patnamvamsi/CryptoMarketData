# Zerodha Table Creation Fix

## Problem
The application was unable to create tables for Zerodha ticker symbols due to several issues in the database layer.

## Root Causes Identified

### 1. Missing Exchange Parameter in Table Creation
**Issue**: `create_kline_binance_table()` function didn't accept an `exchange` parameter, so it always created tables with 'binance' prefix even for Zerodha data.

**File**: `app/db/timescaledb/crud.py`

**Fixed**: Updated function signature to accept `exchange='binance'` parameter and pass it to `get_table_name()`.

### 2. Temp Table Schema Mismatch
**Issue**: `temp_kline_zerodha` table was defined with `TIMESTAMPTZ` columns, but the `insert_kline_rows()` function expects `NUMERIC` (epoch timestamps).

**File**: `app/db/timescaledb/sql_scripts/setup_db.sql`

**Fixed**: Changed `open_time` and `close_time` columns from `TIMESTAMPTZ` to `NUMERIC`.

### 3. Datetime to Epoch Conversion Missing
**Issue**: Zerodha adapter returns datetime objects, but the database layer expects epoch milliseconds.

**Files**:
- `app/ingest/zerodha_historical_data.py`
- `app/stream/stream_zerodha_kline.py`

**Fixed**: Added conversion from datetime to millisecond epoch timestamps in `_convert_klines_to_db_format()` and `_handle_tick()`.

### 4. Incorrect Parameter Names in insert_kline_rows() Calls
**Issue**: Called `insert_kline_rows()` with parameters `interval=` and `kline_data=`, but the function expects `kline=` and `candle_sticks=`.

**Files**:
- `app/ingest/zerodha_historical_data.py`
- `app/stream/stream_zerodha_kline.py`

**Fixed**: Updated all calls to use correct parameter names matching the function signature.

### 5. None Values in Kline Data Fields
**Issue**: Zerodha doesn't provide some fields like `quote_volume`, `trades`, etc., so they are set to `None`. The `insert_kline_rows()` function tries to convert these to `float(None)`, which raises a TypeError.

**Files**:
- `app/ingest/zerodha_historical_data.py`
- `app/stream/stream_zerodha_kline.py`

**Fixed**: Updated conversion logic to use `or 0` to replace all `None` values with 0 before passing to the database layer.

## Files Modified

1. ✅ `app/db/timescaledb/crud.py`
   - Updated `create_kline_binance_table()` to accept `exchange` parameter
   - Updated `create_table_if_not_exists()` to pass `exchange` to table creation

2. ✅ `app/db/timescaledb/sql_scripts/setup_db.sql`
   - Fixed `temp_kline_zerodha` schema to use NUMERIC timestamps

3. ✅ `app/ingest/zerodha_historical_data.py`
   - Added datetime-to-millisecond conversion in `_convert_klines_to_db_format()`

4. ✅ `app/stream/stream_zerodha_kline.py`
   - Added datetime-to-millisecond conversion in `_handle_tick()`

5. ✅ `app/db/timescaledb/sql_scripts/fix_zerodha_temp_table.sql` (NEW)
   - Migration script to fix existing temp table

## How to Apply the Fix

### Step 1: Fix the Temporary Table (If Already Created)

If you've already run the database setup, you need to drop and recreate the temp table:

```bash
# Connect to your database
psql -d market_data_dev1 -U postgres -h 192.168.0.189

# Run the fix script
\i app/db/timescaledb/sql_scripts/fix_zerodha_temp_table.sql
```

Or run it directly:
```bash
psql -d market_data_dev1 -U postgres -h 192.168.0.189 -f app/db/timescaledb/sql_scripts/fix_zerodha_temp_table.sql
```

**Alternative - Manual Fix:**
```sql
DROP TABLE IF EXISTS temp_kline_zerodha;

CREATE TABLE temp_kline_zerodha(
    open_time NUMERIC,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    close_time NUMERIC,
    quote_asset_volume NUMERIC,
    trades NUMERIC,
    taker_buy_base_asset_volume NUMERIC,
    taker_buy_quote_asset_volume NUMERIC,
    ignore NUMERIC
);
```

### Step 2: Restart the Application

The code changes are already in place, so just restart:

```bash
cd app/
python -m uvicorn main:app --reload --port 8002
```

### Step 3: Verify Table Creation

Check that tables are being created correctly:

```sql
-- List all Zerodha tables
SELECT tablename FROM pg_tables
WHERE tablename LIKE 'zerodha_%'
ORDER BY tablename;

-- Example: Check RELIANCE table
\d zerodha_reliance_kline_1m
```

Expected table name format: `zerodha_{symbol}_kline_{interval}`
- `zerodha_reliance_kline_1m`
- `zerodha_tcs_kline_1m`
- `zerodha_infy_kline_5m`

### Step 4: Test Data Ingestion

```bash
# Update data for a symbol
curl "http://localhost:8002/zerodha/update/marketdata/RELIANCE?exchange_segment=NSE&interval=1m"

# Check if data was inserted
psql -d market_data_dev1 -c "SELECT COUNT(*) FROM zerodha_reliance_kline_1m;"
```

## Verification Queries

```sql
-- Check temp table schema (should have NUMERIC for timestamps)
\d temp_kline_zerodha

-- Check active Zerodha symbols
SELECT symbol, priority, active FROM symbols WHERE exchange = 'zerodha';

-- Check created Zerodha tables
SELECT tablename FROM pg_tables
WHERE schemaname = 'public'
AND tablename LIKE 'zerodha_%'
ORDER BY tablename;

-- Check sample data from RELIANCE
SELECT * FROM zerodha_reliance_kline_1m
ORDER BY open_time DESC
LIMIT 5;
```

## Expected Behavior After Fix

1. ✅ Tables are created with correct naming: `zerodha_{symbol}_kline_{interval}`
2. ✅ Datetime objects are automatically converted to epoch milliseconds
3. ✅ Data is inserted successfully into both temp and main tables
4. ✅ Historical data fetching works for all active Zerodha symbols
5. ✅ Real-time streaming stores data correctly

## Testing the Fix

```python
# Test script to verify table creation
from app.db.timescaledb import timescaledb_connect as c
from app.ingest.zerodha_historical_data import ZerodhaDownloader

session_pool = c.get_session_pool()
session = session_pool()

try:
    downloader = ZerodhaDownloader(session, exchange_segment='NSE', interval='1m')

    # This should create the table if it doesn't exist
    result = downloader.fetch_recent_historical_data('RELIANCE')
    print(result)

    # Check if table was created
    from app.db.timescaledb import crud
    table_name = crud.get_table_name('RELIANCE', '1m', 'zerodha')
    print(f"Table name: {table_name}")  # Should print: zerodha_reliance_kline_1m

finally:
    session.close()
```

## Rollback (If Needed)

If you need to rollback:

1. Restore original `crud.py` from git
2. Restore original `setup_db.sql` from git
3. Drop any created Zerodha tables:
   ```sql
   DROP TABLE IF EXISTS temp_kline_zerodha;
   -- Drop individual symbol tables if needed
   DROP TABLE IF EXISTS zerodha_reliance_kline_1m;
   ```

## Support

If you still encounter issues:

1. Check the application logs for detailed error messages
2. Verify the temp table schema: `\d temp_kline_zerodha`
3. Verify access token is valid: `cat zerodha_access_token.txt`
4. Check if unified symbols table exists: `SELECT * FROM symbols WHERE exchange='zerodha' LIMIT 5;`

## Summary

All fixes are now in place. The key changes ensure that:
- Table creation functions accept and use the `exchange` parameter correctly
- Temporary tables use consistent NUMERIC timestamp format
- Datetime objects from Zerodha are converted to epoch milliseconds
- Both historical data fetching and real-time streaming work correctly
