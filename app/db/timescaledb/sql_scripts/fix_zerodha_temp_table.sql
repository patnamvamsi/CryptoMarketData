-- Migration script to fix temp_kline_zerodha table schema
-- Run this if you already created the temp table with TIMESTAMPTZ columns
-- This script drops and recreates it with NUMERIC columns (epoch format)

-- Drop existing temp table if it exists
DROP TABLE IF EXISTS temp_kline_zerodha;

-- Recreate with correct schema (NUMERIC for timestamps)
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

COMMENT ON TABLE temp_kline_zerodha IS 'Temporary staging table for Zerodha kline data processing (uses epoch timestamps)';

-- Verify the schema
\d temp_kline_zerodha
