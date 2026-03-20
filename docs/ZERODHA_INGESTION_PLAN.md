# Zerodha Complete Data Ingestion Plan
> Created: 2026-03-07 | Status: Phase 1 ready to execute

## Rate Limits (Official Kite Connect v3 Docs)

| Endpoint | Limit |
|----------|-------|
| **Historical candles** | **3 requests/second** |
| Quotes | 1 req/sec |
| All other | 10 req/sec |
| HTTP 429 | Throttled — back off and retry |

**Token expires daily** → auto-re-auth via TOTP before each session.  
Auth module: `app/auth/zerodha_auto_auth.py`

---

## Universe Summary

| Exchange | Segment | Instruments | Unique Underlyings |
|----------|---------|-------------|-------------------|
| **NSE** | Equities | 9,276 | 9,276 |
| **NSE** | Indices | 136 (78 with 1-min) | 78 |
| **NFO** | Futures + Options | 43,847 live | 211 underlyings |
| **BSE** | Equities | 12,593 (10,363 BSE-only) | 12,593 |
| **BSE** | Indices | 71 (~17+ with 1-min) | 71 |
| **MCX** | Commodities F&O | 33,023 live | 29 commodities |
| **MCX** | Indices | 11 (7 with 1-min) | 7 |
| **CDS** | Currency F&O | 8,194 live | 14 (USDINR, EURINR, GBPINR, JPYINR + govt bonds) |
| **BFO** | BSE F&O | 5,817 live | 3 underlyings |

---

## Data Depth (Confirmed by Testing — 2026-03-07)

| Interval | Available From | Max per API request |
|----------|---------------|---------------------|
| 1-minute | **~2015** (11 years) | 60 days |
| 3-minute | ~2015 | 100 days |
| 5-minute | ~2015 | 100 days |
| 10-minute | ~2015 | 100 days |
| 15-minute | ~2015 | 200 days |
| 30-minute | ~2015 | 200 days |
| 60-minute | ~2015 | 400 days |
| Day | **~2010** (16 years) | 2000 days |

### Important Limitations
- **Continuous futures** (`continuous=1`): Only works with **`day` interval**. Stitches expired contracts going back to ~2010.
- **F&O intraday**: Only available for **live contracts**. Expired contract tokens are flushed by the exchange — you cannot fetch 1-min data for an expired NIFTY FUT retrospectively. Must cache instrument lists daily to capture tokens before expiry.
- **No tick data**: Zerodha provides OHLCV candles only, not raw ticks.
- **MCX extended hours**: Commodities trade 9 AM – 11:30 PM IST → ~750 1-min candles/day (vs 375 for equities).

---

## Phases

### Phase 1: NSE Indices — All Intervals ⚡
**Time: ~30 minutes**

- **78 indices** with confirmed 1-min data availability
- 11 years of data (2015–2026)
- All intervals: 1m, 5m, 15m, 60m, day
- API calls: ~5,200
- **~160M candles**, ~3 GB Parquet

**Key indices include:**
- Benchmark: NIFTY 50, NIFTY BANK, NIFTY NEXT 50, NIFTY 100, NIFTY 200, NIFTY 500, INDIA VIX
- Sectoral (16): Auto, Commodities, Consumer Durables, Consumption, Energy, Financial Services, FMCG, Healthcare, Infra, IT, Media, Metal, Oil & Gas, Pharma, PSE, Realty
- Thematic/Strategy: Alpha 50, Low Vol, Quality, Momentum, ESG, Dividend, Equal Weight, Multi-cap, Manufacturing, Digital
- Cap-based: Midcap 50/100/150, Smallcap 50/100/250, Microcap 250, Large-Mid 250, Total Market
- Others: PSU Bank, Private Bank, MNC, CPSE, Gilt indices (GS series)

**78 confirmed index tokens:**
```
HANGSENG BEES-NAV (264713), INDIA VIX (264969), NIFTY 100 (260617),
NIFTY 200 (264457), NIFTY 50 (256265), NIFTY 500 (268041),
NIFTY ALPHA 50 (265993), NIFTY ALPHALOWVOL (273673), NIFTY AUTO (263433),
NIFTY BANK (260105), NIFTY COMMODITIES (257289), NIFTY CONSR DURBL (288777),
NIFTY CONSUMPTION (257545), NIFTY CPSE (268297), NIFTY DIV OPPS 50 (257033),
NIFTY ENERGY (261641), NIFTY FIN SERVICE (257801), NIFTY FINSRV25 50 (288265),
NIFTY FMCG (261897), NIFTY GROWSECT 15 (270345), NIFTY GS 10YR (269065),
NIFTY GS 10YR CLN (269321), NIFTY GS 11 15YR (269577),
NIFTY GS 15YRPLUS (269833), NIFTY GS 4 8YR (268553),
NIFTY GS 8 13YR (268809), NIFTY GS COMPSITE (270089),
NIFTY HEALTHCARE (288521), NIFTY IND DIGITAL (291337),
NIFTY INDIA MFG (291081), NIFTY INFRA (261385), NIFTY IT (259849),
NIFTY LARGEMID250 (289545), NIFTY M150 QLTY50 (290825),
NIFTY MEDIA (263945), NIFTY METAL (263689), NIFTY MICROCAP250 (290569),
NIFTY MID LIQ 15 (270601), NIFTY MID SELECT (288009),
NIFTY MIDCAP 100 (256777), NIFTY MIDCAP 150 (266249),
NIFTY MIDCAP 50 (260873), NIFTY MIDSML 400 (266505),
NIFTY MIDSML HLTH (295689), NIFTY MNC (262153),
NIFTY MULTI INFRA (295433), NIFTY MULTI MFG (295177),
NIFTY NEXT 50 (270857), NIFTY OIL AND GAS (289033),
NIFTY PHARMA (262409), NIFTY PSE (262665), NIFTY PSU BANK (262921),
NIFTY PVT BANK (271113), NIFTY REALTY (261129),
NIFTY SERV SECTOR (263177), NIFTY SMLCAP 100 (267017),
NIFTY SMLCAP 250 (267273), NIFTY SMLCAP 50 (266761),
NIFTY TATA 25 CAP (294921), NIFTY TOTAL MKT (290313),
NIFTY100 EQL WGT (271881), NIFTY100 ESG (291593),
NIFTY100 LIQ 15 (267785), NIFTY100 LOWVOL30 (272137),
NIFTY100 QUALTY30 (272393), NIFTY100ESGSECLDR (289801),
NIFTY200 ALPHA 30 (294409), NIFTY200 QUALTY30 (265737),
NIFTY200MOMENTM30 (290057), NIFTY50 DIV POINT (265225),
NIFTY50 EQL WGT (271625), NIFTY50 PR 1X INV (259081),
NIFTY50 PR 2X LEV (258825), NIFTY50 TR 1X INV (259593),
NIFTY50 TR 2X LEV (259337), NIFTY50 VALUE 20 (267529),
NIFTY500 MULTICAP (289289), NIFTYM150MOMNTM50 (294665)
```

---

### Phase 2: NSE FnO Stocks — 1-min + All Intervals 🔥
**Time: ~2 hours**

- **211 stocks** with active F&O contracts (most liquid NSE stocks)
- 11 years of 1-min data each
- All intervals: 1m, 5m, 15m, 60m, day
- API calls: ~14,100 (1-min only) + ~8,000 (other intervals) ≈ **22,000 calls**
- **~500M candles**, ~8 GB Parquet

---

### Phase 3: Remaining NSE Equities — 1-min + Daily 📊
**Time: ~56 hours (1-min) + ~2.5 hours (daily)**

- **~9,065 stocks** (9,276 total minus 211 done in Phase 2)
- Many will have shorter histories (newly listed, illiquid)
- API calls: ~607,000 (1-min) + ~27,000 (daily)
- **Strategy**: 
  - Fetch daily first (quick, ~2.5 hours)
  - Then 1-min: skip symbols returning empty for >2 consecutive years (dead/delisted)
  - Prioritize by trading volume (most active first)
- **~1-2B candles**, ~15-25 GB Parquet

---

### Phase 4: NFO — Futures & Options 📈
**Time: ~1 hour**

- **Continuous daily futures** with OI for all 211 underlyings (back to 2010)
- **Live futures intraday**: All 627 live futures contracts, all intervals
- **Live options intraday**: Current + next 2 expiries for all underlyings
- API calls: ~10,000
- **~200M candles**, ~4 GB Parquet

**Critical ongoing requirement**: Cache instrument token lists daily. Expired contract tokens are flushed — once a contract expires, you lose the ability to fetch its intraday data forever. Must build a daily instrument archiver.

---

### Phase 5: BSE Equities + Indices 📊
**Time: ~65 hours**

- **12,593 stocks** total
  - 2,230 cross-listed with NSE: fetch daily only (1-min already from NSE)
  - 10,363 BSE-only: fetch 1-min + daily
- **71 BSE indices**: 1-min for those that support it, daily for all
- API calls: ~700,000
- **~1-2B candles**, ~15-25 GB Parquet

---

### Phase 6: MCX Commodities ⛏️
**Time: ~2 hours**

- **29 commodities** (Gold, Silver, Crude, Copper, Zinc, Nickel, Natural Gas, Cotton, etc.)
- Live contracts: all intervals with OI
- Continuous daily futures with OI (back to 2010)
- **7 MCX indices** with 1-min: MCXGOLDEX, MCXMETLDEX, MCXCRUDEX, MCXCOPRDEX, MCXCOMPDEX, MCXBULLDEX, MCXSILVDEX
- Extended trading hours (9 AM – 11:30 PM IST) → ~750 candles/day
- API calls: ~15,000
- **~50M candles**, ~1 GB Parquet

---

### Phase 7: CDS & BFO — Currency & BSE F&O 💱
**Time: ~30 minutes**

- **CDS — 14 underlyings**: USDINR, EURINR, GBPINR, JPYINR + govt bond futures (601GS2030, 633GS2035, etc.)
- **BFO — 3 underlyings**: SENSEX, BANKEX options/futures
- Live contracts: all intervals with OI
- Continuous daily with OI
- API calls: ~5,000
- **~20M candles**, ~0.5 GB Parquet

---

## Total Estimates

### Storage
| Phase | Candles (est.) | Parquet Size |
|-------|---------------|-------------|
| 1: NSE Indices | ~160M | ~3 GB |
| 2: FnO Stocks | ~500M | ~8 GB |
| 3: Remaining NSE | ~1-2B | ~15-25 GB |
| 4: NFO F&O | ~200M | ~4 GB |
| 5: BSE | ~1-2B | ~15-25 GB |
| 6: MCX | ~50M | ~1 GB |
| 7: CDS/BFO | ~20M | ~0.5 GB |
| **Total** | **~2-4B candles** | **~50-70 GB Parquet** |

### Time at 3 req/sec
| Phase | API Calls | Wall Time |
|-------|-----------|-----------|
| 1: NSE Indices | ~5,200 | ~30 min |
| 2: FnO Stocks | ~22,000 | ~2 hr |
| 3: NSE Equities | ~634,000 | ~59 hr |
| 4: NFO | ~10,000 | ~1 hr |
| 5: BSE | ~700,000 | ~65 hr |
| 6: MCX | ~15,000 | ~1.5 hr |
| 7: CDS/BFO | ~5,000 | ~30 min |
| **Total** | **~1.39M calls** | **~128 hours (~5.3 days)** |

---

## Architecture

### File Storage Layout
```
/media/vboxuser/test/NSE_Data/zerodha_intraday/
├── NSE/
│   ├── indices/
│   │   ├── 1minute/{SYMBOL}/YYYY-MM.parquet
│   │   ├── 5minute/{SYMBOL}/YYYY-MM.parquet
│   │   ├── 15minute/{SYMBOL}/YYYY-MM.parquet
│   │   ├── 60minute/{SYMBOL}/YYYY-MM.parquet
│   │   └── day/{SYMBOL}/YYYY.parquet
│   └── equities/
│       ├── 1minute/{SYMBOL}/YYYY-MM.parquet
│       └── day/{SYMBOL}/YYYY.parquet
├── NFO/
│   ├── continuous_daily/{UNDERLYING}.parquet
│   ├── futures/{CONTRACT}/1minute/...
│   └── options/{CONTRACT}/1minute/...
├── BSE/
│   ├── indices/...
│   └── equities/...
├── MCX/
│   ├── indices/...
│   ├── continuous_daily/{COMMODITY}.parquet
│   └── futures/{CONTRACT}/...
├── CDS/...
├── BFO/...
└── instruments/
    └── YYYY-MM-DD/  (daily instrument list snapshots)
        ├── NSE.csv
        ├── NFO.csv
        ├── BSE.csv
        ├── MCX.csv
        ├── CDS.csv
        └── BFO.csv
```

### Progress Tracking
- JSON per phase: `zerodha_progress_{phase}.json`
- Per-symbol tracking: last completed date, total candles, errors
- Fully resumable from any interruption point

### Rate Limiter
- Built-in 3 req/sec with exponential backoff on HTTP 429
- Token refresh on HTTP 403 (TokenException)
- Connection retry on network errors

### Daily Auto-Operations (Post-Backfill)
1. **08:30 IST**: Auto re-auth (TOTP login → new access token)
2. **08:35 IST**: Download instrument lists from all exchanges (archive tokens)
3. **16:30 IST**: Fetch previous day's data for all instruments
4. **On new F&O listing**: Detect new contracts, start fetching immediately
5. **Optional**: WebSocket streaming during market hours for real-time data

---

## Dependencies
- `kiteconnect` — Zerodha API client
- `pyotp` — TOTP generation for auto-auth
- `pandas`, `pyarrow` — Data handling + Parquet
- `psycopg2` — TimescaleDB loading (when DB is available)

## Config (.env)
```
ZERODHA_API_KEY=pv8jjbv19goiaj0m
ZERODHA_SECRET_KEY=mmman8h6wbqh4vp8wne60edcw3vslky4
ZERODHA_USERNAME=FL4525
ZERODHA_PASSWORD=Au$$ie640
ZERODHA_TOTP_KEY=54XJTZ6Z3FVE2ZOYU5S2RTGKUWZJ5I2Q
ZERODHA_TOTP_OFFSET=0
ENABLE_ZERODHA_AUTO_AUTH=True
ZERODHA_EXCHANGE_SEGMENT=NSE
ZERODHA_DEFAULT_INTERVAL=1m
ENABLE_ZERODHA_STREAMING=True
ENABLE_ZERODHA_GAP_FILL=True
```
