 
select * from binance_symbols
 
SELECT EXISTS (
   SELECT * FROM information_schema.tables
   WHERE  table_schema = 'public'
   AND    table_name   like '%1m_%'
   );

select max(open_time) from  xrpaud
select to_timestamp(open_time), to_timestamp(close_time/1000) from xrpaud


    SELECT EXISTS(
            SELECT 1
            FROM   information_schema.tables
            WHERE  table_schema = 'public'
            AND    table_name = 'xrpusdc_kline_1m_binance'
        )

select * into inchbtc_kline_1d_binance  from binance_xrpaud_kline_1m

--drop table inchbtc_kline_1d_binance

delete from xrpaud where open_time in (
select open_time from xrpaud
group by open_time
having count(open_time) > 1)

select 'drop table '||tablename||';' from pg_tables where tablename like 'binance%'

INSERT INTO binance_xrpaud_kline_1m
SELECT to_timestamp(open_time) ,
        open,
        high,
        low,
        close,
        volume ,
        to_timestamp(close_time/1000),
        quote_asset_volume,
        trades,
        taker_buy_base_asset_volume,
        taker_buy_quote_asset_volume,
        ignore
FROM xrpaud
ON CONFLICT (open_time)
DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume,
        close_time = EXCLUDED.close_time,
        quote_asset_volume = EXCLUDED.quote_asset_volume,
        trades = EXCLUDED.trades,
        taker_buy_base_asset_volume = EXCLUDED.taker_buy_base_asset_volume,
        taker_buy_quote_asset_volume = EXCLUDED.taker_buy_quote_asset_volume,
        ignore = EXCLUDED.ignore
;


 select to_timestamp(open_time) from xrpaud
 select * from binance_xrpaud_kline_1m

 --drop table binance_xrpaud_kline_1m

  CREATE TABLE if not exists binance_xrpaud_kline_1m(
        open_time TIMESTAMPTZ,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume NUMERIC ,
        close_time TIMESTAMPTZ,
        quote_asset_volume NUMERIC,
        trades NUMERIC,
        taker_buy_base_asset_volume NUMERIC,
        taker_buy_quote_asset_volume NUMERIC,
        ignore NUMERIC
    );



SELECT create_hypertable('binance_xrpaud_kline_1m', 'open_time' );

CREATE UNIQUE INDEX idx_binance_xrpaud_kline_1m ON binance_xrpaud_kline_1m(open_time);



SELECT NOT EXISTS (
   SELECT  FROM information_schema.tables
   WHERE  table_schema = 'public'
   AND    table_name   = 'temp_kline_binance'
   );


--drop table binance_symbols;

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


--truncate table  binance_symbols

select count(*) from binance_symbols


select count(open_time) from binance_xrpaud_kline_1m


SELECT version,last_updated,priority,* FROM binance_symbols WHERE symbol = 'BTCAUD'


SELECT version,last_updated,priority,* FROM binance_symbols WHERE version > 7


UPDATE binance_symbols
SET priority = ,
active = True,
version = version+1
last_updated = CURRENT_TIMESTAMP
WHERE symbol =


UPDATE binance_symbols  p
    SET
    status= T.status,
    baseAsset = T.baseAsset,
    baseAssetPrecision  = T.baseAssetPrecision,
    quoteAsset = T.quoteAsset,
    quotePrecision  = T.quotePrecision ,
    quoteAssetPrecision  = T.quoteAssetPrecision ,
    baseCommissionPrecision  = T.baseCommissionPrecision ,
    quoteCommissionPrecision  = T.quoteCommissionPrecision ,
    orderTypes  = T.orderTypes ,
    icebergAllowed   = T.icebergAllowed  ,
    ocoAllowed = T.ocoAllowed ,
    quoteOrderQtyMarketAllowed  = T.quoteOrderQtyMarketAllowed ,
    allowTrailingStop = T.allowTrailingStop ,
    isSpotTradingAllowed  = T. isSpotTradingAllowed ,
    isMarginTradingAllowed  = T.isMarginTradingAllowed  ,
    filters  = T.filters ,
    permissions  = T.permissions ,
    version = P.version + 1,
    last_updated = T.last_updated
    FROM temp_binance_symbols  T WHERE p.symbol = T.symbol






select * from binance_symbols
select * from binance_xrpaud_kline_1m limit 10

select distinct date(open_time) from binance_xrpaud_kline_1m where open_time > '2022-09-01' order by 1 desc
select distinct date(open_time) from binance_btcaud_kline_1m where open_time > '2022-09-01' order by 1 desc
--limit 10

--to find gaps and fill them
with temp_table as (
SELECT
  time_bucket_gapfill('1 min', open_time) AS gap,
	avg(open  ) as val
FROM binance_xrpaud_kline_1m
WHERE open_time > now() - INTERVAL '1 month' AND open_time < now()
GROUP BY gap)

select max(gap) from temp_table where val is null




WITH gaps AS (
  SELECT
    open_time AS gap_start,
    LEAD(open_time) OVER (ORDER BY open_time) AS gap_end
  FROM
    binance_btcusdt_kline_1m
)
SELECT
  gap_start,
  gap_end,
  EXTRACT(EPOCH FROM (gap_end - gap_start)) AS gap_duration
FROM
  gaps
WHERE
  EXTRACT(EPOCH FROM (gap_end - gap_start)) > 120; -- 120 seconds = 2 minutes

drop table NSE_BHAV_COPY


CREATE TABLE NSE_BHAV_COPY
	(SYMBOL varchar null,
	SERIES char(10) null,
	DATE1 date null,
	PREV_CLOSE float null,
	OPEN_PRICE float null,
	HIGH_PRICE float null,
	LOW_PRICE float null,
	LAST_PRICE float null,
	CLOSE_PRICE float null,
	AVG_PRICE float null,
	TTL_TRD_QNTY bigint null,
	TURNOVER_LACS float null,
	NO_OF_TRADES bigint null,
	DELIV_QTY bigint null,
	DELIV_PER float null
	)