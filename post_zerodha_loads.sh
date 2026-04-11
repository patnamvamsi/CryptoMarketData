#!/bin/bash

LOG="/media/vboxuser/test/NSE_Data/post_zerodha_loads.log"
STATE="/media/vboxuser/test/NSE_Data/post_zerodha_state.txt"
REPO="/home/vboxuser/CryptoMarketData"
PY="$REPO/venv/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
done_check() { grep -q "^DONE:$1$" "$STATE" 2>/dev/null; }
mark_done() { echo "DONE:$1" >> "$STATE"; }

log "=== Waiting for Zerodha phase 3 download to finish ==="
while pgrep -f "zerodha_intraday_ingest" > /dev/null; do
    sleep 30
done
log "Zerodha download done — starting parquet loads"

touch "$STATE"
cd "$REPO"

# Load Zerodha intraday parquet → DB
if ! done_check "zerodha_parquet_db"; then
    log "── Loading Zerodha intraday parquet → DB ──"
    $PY -m app.ingest.historical_data_to_db \
        >> /media/vboxuser/test/NSE_Data/zerodha_db_load.log 2>&1 || \
    $PY -m app.ingest.load_csv_to_db \
        >> /media/vboxuser/test/NSE_Data/zerodha_db_load.log 2>&1
    log "Zerodha parquet load done (exit $?)"
    mark_done "zerodha_parquet_db"
fi

log "=== All post-zerodha loads complete ==="
echo "EXIT_CODE:0" >> "$LOG"
