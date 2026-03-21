# Fundamental Data Plan

This document describes the two fundamental/alternative data sources integrated
into the CryptoMarketData service: **NSE Corporate Events** and **GDELT News Sentiment**.

---

## 1. NSE Corporate Events

### Source
NSE India public API — no authentication or API key required, but session cookies
must be obtained by first fetching the NSE homepage.

**Endpoint:**
```
https://www.nseindia.com/api/corporates-corporateActions
  ?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
```

**Coverage:** Dividends, bonus issues, stock splits, rights issues, earnings results,
board meeting notices, AGM/EGM announcements.

---

### Schema

Saved as Parquet at:
```
/media/vboxuser/test/NSE_Data/corporate_events/YYYY/MM.parquet
```

| Column           | Type      | Description                                      |
|------------------|-----------|--------------------------------------------------|
| `symbol`         | str       | NSE trading symbol (e.g. RELIANCE)               |
| `company_name`   | str       | Full company name                                |
| `series`         | str       | Market series (EQ, BE, …)                       |
| `ex_date`        | datetime  | Ex-date for the corporate action                 |
| `purpose`        | str       | Nature of action (DIVIDEND, BONUS, SPLIT, etc.)  |
| `record_date`    | str       | Record date (if provided)                        |
| `bc_start_date`  | str       | Book closure start                               |
| `bc_end_date`    | str       | Book closure end                                 |
| `nd_start_date`  | str       | No-delivery period start                         |
| `nd_end_date`    | str       | No-delivery period end                           |
| `actual_ex_date` | str       | Actual ex-date (if revised)                      |
| `_year`          | int       | Partition year                                   |
| `_month`         | int       | Partition month                                  |

All additional fields returned by the NSE API are preserved as-is in the Parquet file.

---

### Update Frequency

| Mode        | Schedule            | CLI command                                    |
|-------------|---------------------|------------------------------------------------|
| Daily       | 18:00 IST Mon–Fri   | `python manage.py ingest corporate-events`     |
| Backfill    | One-off             | `python manage.py ingest corporate-events --start 2015-01-01` |
| Standalone  | On demand           | `python -m app.ingest.nse_corporate_events --daily` |
| Standalone  | On demand           | `python -m app.ingest.nse_corporate_events --backfill` |

Default backfill start: **2010-01-01**

---

### Resume Safety

Progress is tracked in:
```
/media/vboxuser/test/NSE_Data/corporate_events_progress.json
```

Completed months (as `"YYYY-MM"` strings) are skipped on restart. Delete the
JSON file to force a full re-fetch.

---

### Backtesting Use Cases

1. **Dividend-adjusted returns** — join `ex_date` to OHLCV data to apply corporate
   action adjustments.
2. **Event-driven strategies** — filter for `purpose = BONUS` or `SPLIT` and
   measure price impact in the ±5-day window around `ex_date`.
3. **Earnings surprise** — pair `purpose = RESULTS` events with OHLCV to study
   post-earnings drift.
4. **Gap risk** — flag trading days that land inside a book-closure period.

```python
import pandas as pd

# Load a year of events
df = pd.read_parquet(
    "/media/vboxuser/test/NSE_Data/corporate_events/2024/03.parquet"
)

# Filter dividends only
dividends = df[df["purpose"].str.upper().str.contains("DIVIDEND", na=False)]

# Join to price data on ex_date
# ...
```

---

## 2. GDELT News Sentiment

### Source
GDELT 2.0 Global Knowledge Graph (GKG) — completely free, no registration required.

**Master file list:**
```
http://data.gdeltproject.org/gdeltv2/masterfilelist.txt
```

Files are generated every 15 minutes. Each line in the master list has three fields:
```
<size_bytes>  <md5_hash>  <url>
```

GKG URLs follow the pattern:
```
http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip
```

**Coverage:** 15-minute snapshots of global news, scraped from thousands of sources.
Includes tone/sentiment scores, theme tags, location mentions, and entity extraction.

---

### India Relevance Filter

A row is included if **any** of these conditions is true:

- `V2Locations` contains `"India"` or `"IN#"` (ISO code prefix)
- `Locations` (V1) contains `"India"`
- `SourceCommonName` matches a known Indian news outlet (Times of India, NDTV,
  Economic Times, Business Standard, Mint, Moneycontrol, etc.)
- `Themes` or `V2Themes` contain `"INDIA"` (case-insensitive)

---

### Schema

Saved as Parquet at:
```
/media/vboxuser/test/NSE_Data/gdelt_sentiment/YYYY/MM/DD.parquet
```

| Column                  | Type      | Description                                   |
|-------------------------|-----------|-----------------------------------------------|
| `datetime`              | datetime  | UTC timestamp of the GKG record               |
| `date`                  | date      | Calendar date (partition key)                 |
| `source`                | str       | SourceCommonName (news outlet)                |
| `url`                   | str       | Article URL (DocumentIdentifier)              |
| `tone`                  | float     | Overall tone (positive = higher)              |
| `positive_score`        | float     | Fraction of positive words                    |
| `negative_score`        | float     | Fraction of negative words                    |
| `polarity`              | float     | (positive − negative)                         |
| `activity_ref_density`  | float     | Activity reference density                    |
| `self_ref_density`      | float     | Self-reference density                        |
| `word_count`            | float     | Total words in article                        |
| `themes`                | list[str] | V2Themes tags                                 |
| `locations`             | list[str] | V2Locations entries                           |
| `persons`               | list[str] | V2Persons mentions                            |
| `organizations`         | list[str] | V2Organizations mentions                      |
| `gkg_record_id`         | str       | Raw GKGRECORDID (dedup key)                   |

---

### Update Frequency

| Mode        | Schedule       | CLI command                                     |
|-------------|----------------|-------------------------------------------------|
| Latest      | Every 6 hours  | `python manage.py ingest gdelt`                 |
| Backfill    | One-off        | `python manage.py ingest gdelt --start 2024-01-01` |
| Standalone  | On demand      | `python -m app.ingest.gdelt_ingest --latest`    |
| Standalone  | On demand      | `python -m app.ingest.gdelt_ingest --backfill`  |

Default backfill window: **last 2 years**

---

### Resume Safety

Progress is tracked in:
```
/media/vboxuser/test/NSE_Data/gdelt_progress.json
```

Completed filenames (e.g. `20240315120000.gkg.csv.zip`) are skipped on restart.

---

### Backtesting Use Cases

1. **Market sentiment overlay** — aggregate daily average `tone` across Indian sources
   and compare to Nifty 50 returns. A sharp drop in sentiment often precedes selling.

2. **Event detection** — scan `themes` for `"ECON_BANKRUPTCY"`, `"FRAUD"`,
   `"PROTEST"`, etc. to identify exogenous shock dates.

3. **Pre-earnings news flow** — count articles mentioning a company in `organizations`
   in the 7 days before its earnings `ex_date` (join with corporate events table).

4. **Sector rotation signals** — filter `themes` for sector tags (`"ECON_BANKS"`,
   `"ENV_CLIMATECHANGE"`, etc.) and compute rolling sentiment z-scores.

```python
import pandas as pd
from pathlib import Path

# Load a day of India sentiment
df = pd.read_parquet(
    "/media/vboxuser/test/NSE_Data/gdelt_sentiment/2024/03/15.parquet"
)

# Daily aggregate tone
daily_tone = df.groupby("date")["tone"].agg(["mean", "std", "count"])
print(daily_tone)

# Articles mentioning RELIANCE
reliance = df[df["organizations"].apply(
    lambda orgs: any("Reliance" in o for o in (orgs or []))
)]
print(reliance[["datetime", "source", "tone", "url"]].head())
```

---

## Combined Strategy Example

```python
# 1. Load corporate events for a symbol
events = pd.read_parquet(
    "/media/vboxuser/test/NSE_Data/corporate_events/2024/01.parquet"
)
reliance_events = events[events["symbol"] == "RELIANCE"]

# 2. Load GDELT sentiment for the week before ex_date
for _, event in reliance_events.iterrows():
    ex_date = event["ex_date"].date()
    for day_offset in range(-7, 1):
        day = ex_date + timedelta(days=day_offset)
        path = Path(
            f"/media/vboxuser/test/NSE_Data/gdelt_sentiment/"
            f"{day.year}/{day.month:02d}/{day.day:02d}.parquet"
        )
        if path.exists():
            sentiment = pd.read_parquet(path)
            # ... compute pre-event sentiment signal
```

---

## Storage Layout Summary

```
/media/vboxuser/test/NSE_Data/
├── corporate_events/
│   ├── 2010/
│   │   └── 01.parquet  ← Jan 2010 corporate actions
│   ├── ...
│   └── 2026/
│       └── 03.parquet
├── corporate_events_progress.json
├── corporate_events_ingest.log
│
├── gdelt_sentiment/
│   ├── 2024/
│   │   ├── 01/
│   │   │   ├── 01.parquet  ← 2024-01-01 India news
│   │   │   └── ...
│   │   └── ...
│   └── ...
├── gdelt_progress.json
└── gdelt_ingest.log
```

---

## Dependencies

Both ingesters use only standard libraries already available in the project:

| Package       | Use                              |
|---------------|----------------------------------|
| `requests`    | HTTP downloads                   |
| `pandas`      | DataFrame processing             |
| `pyarrow`     | Parquet read/write               |
| `zipfile`     | In-memory GDELT zip extraction   |

No additional pip installs are required.
