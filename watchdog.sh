#!/bin/bash
# watchdog.sh — Monitors and restarts loader chain if anything dies
# Run via cron every 15 minutes
#
# Outputs a JSON status summary to /tmp/dataload_status.json

REPO=/home/vboxuser/CryptoMarketData
PY=$REPO/venv/bin/python3
LOG=/tmp/watchdog.log
STATUS_FILE=/tmp/dataload_status.json

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "--- watchdog tick ---"

# ---------------------------------------------------------------------------
# Helper: get last N lines of a log file
# ---------------------------------------------------------------------------
last_line() { tail -1 "$1" 2>/dev/null || echo "no log"; }

# ---------------------------------------------------------------------------
# Check what's running
# ---------------------------------------------------------------------------
ZERODHA_PID=$(pgrep -f "zerodha_ohlcv_loader" | head -1)
CHAIN_PID=$(pgrep -f "run_chain.sh" | head -1)
CRYPTO_PID=$(pgrep -f "crypto_csv_loader" | head -1)
YFINANCE_PID=$(pgrep -f "yfinance_db_loader" | head -1)
OPTIONS_PID=$(pgrep -f "options_iv_backfill" | head -1)

log "PIDs: zerodha=$ZERODHA_PID chain=$CHAIN_PID crypto=$CRYPTO_PID yfinance=$YFINANCE_PID options=$OPTIONS_PID"

# ---------------------------------------------------------------------------
# Parse zerodha progress
# ---------------------------------------------------------------------------
ZERODHA_PROGRESS=$(grep -o '\[.*\]' /tmp/zerodha_resume.log 2>/dev/null | tail -1)
ZERODHA_ROWS=$(grep "running total:" /tmp/zerodha_resume.log 2>/dev/null | tail -1 | grep -o '[0-9,]*$' | tr -d ',')

# ---------------------------------------------------------------------------
# Check crypto CSV progress
# ---------------------------------------------------------------------------
CRYPTO_PROGRESS_FILE="/media/vboxuser/test/NSE_Data/crypto_csv_loader_progress.json"
CRYPTO_FILES_DONE=0
CRYPTO_ROWS=0
if [ -f "$CRYPTO_PROGRESS_FILE" ]; then
    CRYPTO_FILES_DONE=$(python3 -c "import json; p=json.load(open('$CRYPTO_PROGRESS_FILE')); print(len(p.get('processed_files',[])))" 2>/dev/null || echo 0)
    CRYPTO_ROWS=$(python3 -c "import json; p=json.load(open('$CRYPTO_PROGRESS_FILE')); print(p.get('total_inserted',0))" 2>/dev/null || echo 0)
fi

# ---------------------------------------------------------------------------
# Check yfinance progress
# ---------------------------------------------------------------------------
YF_PROGRESS_FILE="/media/vboxuser/test/NSE_Data/yfinance_db_loader_progress.json"
YF_DONE=0
if [ -f "$YF_PROGRESS_FILE" ]; then
    YF_DONE=$(python3 -c "import json; p=json.load(open('$YF_PROGRESS_FILE')); print(len(p.get('done',[])))" 2>/dev/null || echo 0)
fi

# ---------------------------------------------------------------------------
# Check options IV progress
# ---------------------------------------------------------------------------
IV_PROGRESS_FILE="/media/vboxuser/test/NSE_Data/options_iv_progress.json"
IV_DATES_DONE=0
IV_ROWS=0
if [ -f "$IV_PROGRESS_FILE" ]; then
    IV_DATES_DONE=$(python3 -c "import json; p=json.load(open('$IV_PROGRESS_FILE')); print(len(p.get('done_dates',[])))" 2>/dev/null || echo 0)
    IV_ROWS=$(python3 -c "import json; p=json.load(open('$IV_PROGRESS_FILE')); print(p.get('total_rows',0))" 2>/dev/null || echo 0)
fi

# ---------------------------------------------------------------------------
# Determine current phase
# ---------------------------------------------------------------------------
if [ -n "$ZERODHA_PID" ]; then
    PHASE="A: zerodha_ohlcv_loader running"
elif [ -n "$CRYPTO_PID" ]; then
    PHASE="B1: crypto_csv_loader running"
elif [ -n "$YFINANCE_PID" ]; then
    PHASE="B2: yfinance_db_loader running"
elif [ -n "$OPTIONS_PID" ]; then
    PHASE="B3: options_iv_backfill running"
elif [ -n "$CHAIN_PID" ]; then
    PHASE="chain: waiting/transitioning"
else
    PHASE="idle/done"
fi

# ---------------------------------------------------------------------------
# Write JSON status
# ---------------------------------------------------------------------------
cat > "$STATUS_FILE" << EOF
{
  "timestamp": "$(date -Iseconds)",
  "phase": "$PHASE",
  "zerodha": {
    "pid": "$ZERODHA_PID",
    "progress": "$ZERODHA_PROGRESS",
    "rows_inserted": "${ZERODHA_ROWS:-0}",
    "last_log": "$(last_line /tmp/zerodha_resume.log)"
  },
  "run_chain": {
    "pid": "$CHAIN_PID",
    "last_log": "$(last_line /tmp/run_chain.log)"
  },
  "crypto": {
    "pid": "$CRYPTO_PID",
    "files_done": $CRYPTO_FILES_DONE,
    "rows_inserted": $CRYPTO_ROWS,
    "last_log": "$(last_line /tmp/chain_crypto.log)"
  },
  "yfinance": {
    "pid": "$YFINANCE_PID",
    "symbols_done": $YF_DONE,
    "last_log": "$(last_line /tmp/chain_yfinance.log)"
  },
  "options_iv": {
    "pid": "$OPTIONS_PID",
    "dates_done": $IV_DATES_DONE,
    "rows_inserted": $IV_ROWS,
    "last_log": "$(last_line /tmp/chain_options_iv.log)"
  }
}
EOF

log "Status written to $STATUS_FILE"

# ---------------------------------------------------------------------------
# Recovery: if nothing is running and chain is dead, restart it
# ---------------------------------------------------------------------------
ALL_DONE=false

# Check sentinel file first (written once chain completes, survives daily scheduler resets)
if [ -f "/tmp/run_chain_ALL_DONE" ]; then
    ALL_DONE=true
# Check if all 3 phases are complete
elif [ "$CRYPTO_FILES_DONE" -ge 2820 ] && [ "$YF_DONE" -ge 2200 ] && [ "$IV_DATES_DONE" -ge 5200 ]; then
    ALL_DONE=true
    touch /tmp/run_chain_ALL_DONE
fi

if [ -z "$ZERODHA_PID" ] && [ -z "$CHAIN_PID" ] && [ -z "$CRYPTO_PID" ] && \
   [ -z "$YFINANCE_PID" ] && [ -z "$OPTIONS_PID" ] && [ "$ALL_DONE" = false ]; then
    log "WARNING: Nothing running and not complete! Restarting run_chain.sh"
    cd "$REPO"
    nohup bash run_chain.sh >> /tmp/run_chain.log 2>&1 &
    log "Restarted run_chain.sh as PID $!"
fi

log "--- watchdog done ---"
