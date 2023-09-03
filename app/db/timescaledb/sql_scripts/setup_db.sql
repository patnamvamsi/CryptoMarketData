CREATE TABLE IF NOT EXISTS market_data_source (
    source text,
  	url text
);

CREATE UNIQUE INDEX idx_market_data_source ON market_data_source(source);

INSERT INTO market_data_source values ('binance') ON CONFLICT (source) DO NOTHING;
INSERT INTO market_data_source values ('coinbase') ON CONFLICT (source) DO NOTHING;
INSERT INTO market_data_source values ('CCXT') ON CONFLICT (source) DO NOTHING;



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

