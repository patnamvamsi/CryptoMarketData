CREATE TABLE IF NOT EXISTS market_data_source (
    source text,
  	url text
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_data_source ON market_data_source(source);

INSERT INTO market_data_source values ('binance') ON CONFLICT (source) DO NOTHING;
INSERT INTO market_data_source values ('coinbase') ON CONFLICT (source) DO NOTHING;
INSERT INTO market_data_source values ('CCXT') ON CONFLICT (source) DO NOTHING;
INSERT INTO market_data_source values ('zerodha') ON CONFLICT (source) DO NOTHING;



CREATE TABLE IF NOT EXISTS binance_symbols (
        symbol varchar(20),
        status varchar(20),
        baseAsset varchar(20),
        baseAssetPrecision int,
        quoteAsset varchar(20),
        quotePrecision int,
        quoteAssetPrecision int,
        baseCommissionPrecision int,
        quoteCommissionPrecision int,
        orderTypes json,
        icebergAllowed  boolean,
        ocoAllowed boolean,
        quoteOrderQtyMarketAllowed  boolean,
        allowTrailingStop boolean,
        isSpotTradingAllowed  boolean,
        isMarginTradingAllowed  boolean,
        filters json,
        permissions json,
        priority int,
        active boolean,
        version int,
        last_updated timestamptz
);
CREATE UNIQUE INDEX idx_binance_symbols ON binance_symbols(symbol);


CREATE TABLE IF NOT EXISTS temp_kline_binance(
        open_time NUMERIC,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume NUMERIC ,
        close_time NUMERIC,
        quote_asset_volume NUMERIC,
        trades NUMERIC,
        taker_buy_base_asset_volume NUMERIC,
        taker_buy_quote_asset_volume NUMERIC,
        ignore NUMERIC
    );


-- ================================================================================
-- UNIFIED SYMBOLS TABLE (EXCHANGE-AGNOSTIC)
-- This table stores symbols/instruments from all exchanges
-- ================================================================================
CREATE TABLE IF NOT EXISTS symbols (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(20) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    base_asset VARCHAR(20),
    quote_asset VARCHAR(20),
    status VARCHAR(20) DEFAULT 'TRADING',
    priority INT DEFAULT 0 CHECK (priority >= 0 AND priority <= 10),
    active BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(exchange, symbol)
);

CREATE INDEX IF NOT EXISTS idx_symbols_exchange ON symbols(exchange);
CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active, priority DESC);
CREATE INDEX IF NOT EXISTS idx_symbols_metadata_gin ON symbols USING GIN(metadata);

COMMENT ON TABLE symbols IS 'Unified symbols table for all exchanges';
COMMENT ON COLUMN symbols.exchange IS 'Exchange identifier (binance, zerodha, etc.)';
COMMENT ON COLUMN symbols.symbol IS 'Trading symbol or instrument name';
COMMENT ON COLUMN symbols.metadata IS 'Exchange-specific metadata stored as JSONB (e.g., instrument_token for Zerodha)';


-- ================================================================================
-- ZERODHA TABLES
-- ================================================================================

-- Temporary staging table for Zerodha kline data
CREATE TABLE IF NOT EXISTS temp_kline_zerodha(
        open_time TIMESTAMPTZ,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume NUMERIC,
        close_time TIMESTAMPTZ,
        quote_asset_volume NUMERIC,
        trades NUMERIC,
        taker_buy_base_asset_volume NUMERIC,
        taker_buy_quote_asset_volume NUMERIC,
        ignore NUMERIC
    );

COMMENT ON TABLE temp_kline_zerodha IS 'Temporary staging table for Zerodha kline data processing';

