#!/bin/bash
# run_chain.sh — Post-Zerodha load chain
#
# Waits for zerodha_ohlcv_loader to finish, then runs in sequence:
#   1. crypto_csv_loader  (Binance 1m CSVs → crypto_ohlcv)
#   2. yfinance_db_loader (yfinance JSONs → fundamentals_quarterly + info)
#   3. options_iv_backfill (compute IV+Greeks → options_iv)
#
# Usage:
#   nohup bash run_chain.sh > /tmp/run_chain.log 2>&1 &
#
# Logs:
#   /tmp/run_chain.log         — chain orchestration
#   /tmp/chain_crypto.log      — crypto CSV loader
#   /tmp/chain_yfinance.log    — yfinance DB loader
#   /tmp/chain_options_iv.log  — options IV backfill

set -euo pipefail

REPO=/home/vboxuser/CryptoMarketData
PY=$REPO/venv/bin/python3
LOG=/tmp/run_chain.log

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# ---------------------------------------------------------------------------
# Wait for Zerodha loader to finish
# ---------------------------------------------------------------------------
if pgrep -f "zerodha_ohlcv_loader" > /dev/null 2>&1; then
    log "Zerodha loader is running — waiting for it to finish..."
    while pgrep -f "zerodha_ohlcv_loader" > /dev/null 2>&1; do
        sleep 60
    done
    log "Zerodha loader done."
else
    log "Zerodha loader not running — proceeding directly."
fi

cd "$REPO"

# ---------------------------------------------------------------------------
# Step 1: Crypto 1m CSV → crypto_ohlcv
# ---------------------------------------------------------------------------
CRYPTO_PROGRESS="/media/vboxuser/test/NSE_Data/crypto_csv_loader_progress.json"
if python3 -c "
import json, sys
try:
    p = json.load(open('$CRYPTO_PROGRESS'))
    done = len(p.get('processed_files', []))
    total = p.get('total_inserted', 0)
    print(f'crypto: {done} files done, {total:,} rows')
    # If there are 2820 files done, it's complete
    sys.exit(0 if done >= 2820 else 1)
except: sys.exit(1)
" 2>/dev/null; then
    log "Crypto CSV already complete — skipping."
else
    log "=== Step 1: Loading Binance 1m CSV → crypto_ohlcv ==="
    $PY -m app.ingest.crypto_csv_loader \
        > /tmp/chain_crypto.log 2>&1
    CRYPTO_EXIT=$?
    log "Crypto CSV done (exit $CRYPTO_EXIT)"
    if [ $CRYPTO_EXIT -ne 0 ]; then
        log "Crypto CSV load FAILED. Check /tmp/chain_crypto.log"
        # Don't abort — continue with other steps
    fi
fi

# ---------------------------------------------------------------------------
# Step 2: yfinance JSON → fundamentals_quarterly + fundamentals_info
# ---------------------------------------------------------------------------
YF_PROGRESS="/media/vboxuser/test/NSE_Data/yfinance_db_loader_progress.json"
if python3 -c "
import json, sys, os
try:
    p = json.load(open('$YF_PROGRESS'))
    done = len(p.get('done', []))
    print(f'yfinance: {done} symbols done')
    sys.exit(0 if done >= 2200 else 1)
except: sys.exit(1)
" 2>/dev/null; then
    log "yfinance DB load already complete — skipping."
else
    log "=== Step 2: Loading yfinance JSONs → fundamentals tables ==="
    $PY -m app.ingest.yfinance_db_loader \
        > /tmp/chain_yfinance.log 2>&1
    YF_EXIT=$?
    log "yfinance DB load done (exit $YF_EXIT)"
    if [ $YF_EXIT -ne 0 ]; then
        log "yfinance DB load FAILED. Check /tmp/chain_yfinance.log"
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Options IV + Greeks backfill
# ---------------------------------------------------------------------------
IV_PROGRESS="/media/vboxuser/test/NSE_Data/options_iv_progress.json"
if python3 -c "
import json, sys
try:
    p = json.load(open('$IV_PROGRESS'))
    done = len(p.get('done_dates', []))
    total = p.get('total_rows', 0)
    print(f'options_iv: {done} dates done, {total:,} rows')
    # Rough completeness check: ~6000 trading days 2001-2026
    sys.exit(0 if done >= 5200 else 1)
except: sys.exit(1)
" 2>/dev/null; then
    log "Options IV backfill already complete — skipping."
else
    log "=== Step 3: Computing options IV + Greeks → options_iv ==="
    $PY -m app.ingest.options_iv_backfill \
        --start 2001-01-01 \
        --resume \
        > /tmp/chain_options_iv.log 2>&1
    IV_EXIT=$?
    log "Options IV backfill done (exit $IV_EXIT)"
    if [ $IV_EXIT -ne 0 ]; then
        log "Options IV backfill FAILED. Check /tmp/chain_options_iv.log"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
touch /tmp/run_chain_ALL_DONE
log "=== All chain steps complete ==="
log "Summary:"
for f in /tmp/chain_crypto.log /tmp/chain_yfinance.log /tmp/chain_options_iv.log; do
    [ -f "$f" ] && echo "  $(basename $f): $(tail -1 $f)"
done
