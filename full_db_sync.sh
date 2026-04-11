#!/bin/bash
# Phase 2: Parquet ingest (runs after copy_from_dev1.py completes)

LOG="/media/vboxuser/test/NSE_Data/full_db_sync.log"
STATE="/media/vboxuser/test/NSE_Data/full_db_sync_state.txt"
REPO="/home/vboxuser/CryptoMarketData"
PY="$REPO/venv/bin/python3"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
done_check() { grep -q "^DONE:$1$" "$STATE" 2>/dev/null; }
mark_done() { echo "DONE:$1" >> "$STATE"; }

cd "$REPO"

# Wait for Phase 1 (copy_from_dev1.py) to finish first
log "Waiting for Phase 1 (copy_from_dev1.py) to finish..."
while pgrep -f copy_from_dev1.py > /dev/null; do sleep 30; done
log "Phase 1 complete, starting Phase 2 parquet ingestion"

run_ingest() {
    local key="$1"; shift
    done_check "$key" && log "SKIP: $key" && return
    log "Running: $key"
    "$@"
    log "Done: $key (exit $?)"
    mark_done "$key"
}

log "── Phase 2a: NSE Equity EOD ──"
run_ingest "INGEST:nse_equity" $PY -m app.ingest.nse_bhavcopy_ingest --start 2000-01-01 \
    >> /media/vboxuser/test/NSE_Data/nse_equity_ingest2.log 2>&1

log "── Phase 2b: NSE F&O Bhavcopy ──"
run_ingest "INGEST:nse_fo" $PY -m app.ingest.nse_fo_bhavcopy_ingest --start 2000-06-01 \
    >> /media/vboxuser/test/NSE_Data/nse_fo_ingest2.log 2>&1

log "── Phase 2c: NSE Index Daily ──"
run_ingest "INGEST:nse_index" $PY -m app.ingest.nse_index_daily_ingest --start 2000-01-01 \
    >> /media/vboxuser/test/NSE_Data/nse_index_ingest2.log 2>&1

log "── Phase 2d: Corporate Events ──"
run_ingest "INGEST:corporate" $PY -m app.ingest.nse_corporate_events --backfill \
    >> /media/vboxuser/test/NSE_Data/corporate_events_ingest2.log 2>&1

log "── Phase 2e: FII/DII ──"
run_ingest "INGEST:fii_dii" $PY -m app.ingest.fii_dii_ingest --backfill \
    >> /media/vboxuser/test/NSE_Data/fii_dii_ingest2.log 2>&1

log "── Phase 2f: Global Signals ──"
run_ingest "INGEST:global_signals" $PY -m app.ingest.global_signals_ingest --backfill \
    >> /media/vboxuser/test/NSE_Data/global_signals_ingest2.log 2>&1

log "── Phase 2g: Fundamentals ──"
run_ingest "INGEST:fundamentals" $PY -m app.ingest.fundamentals_ingest --backfill \
    >> /media/vboxuser/test/NSE_Data/fundamentals_ingest2.log 2>&1

log "── Phase 2h: GDELT Sentiment (~7.5GB) ──"
run_ingest "INGEST:gdelt" $PY -m app.ingest.gdelt_ingest --backfill \
    >> /media/vboxuser/test/NSE_Data/gdelt_ingest2.log 2>&1

log "── Phase 2i: Zerodha OHLCV intraday (45GB) ──"
run_ingest "INGEST:zerodha" $PY -m app.ingest.zerodha_intraday_ingest --phase all \
    >> /media/vboxuser/test/NSE_Data/zerodha_ingest2.log 2>&1

log "========================================="
log " ALL DONE!"
log "========================================="
echo "EXIT_CODE:0" >> "$LOG"
