import time
from app.config import config
import datetime
import traceback
import pytz
import logging
from app.logger import setup_logging

logger = logging.getLogger(__name__)
from kiteconnect import KiteConnect, KiteTicker

# -----------------------------------------------------------
# CONFIG
# -----------------------------------------------------------
API_KEY = config.ZERODHA_API_KEY
API_SECRET = config.ZERODHA_SECRET_KEY


# For first login only — after that, you paste REQUEST_TOKEN here
REQUEST_TOKEN = '9BiaPMi2tLt8ZU2jWudcmC8dQdga67Oz'      # set this AFTER your first login
ACCESS_TOKEN_FILE = "access_token.txt"
# -----------------------------------------------------------


# -----------------------------------------------------------
# Save & Load Access Token Locally
# -----------------------------------------------------------
def save_access_token(token: str):
    with open(ACCESS_TOKEN_FILE, "w") as f:
        f.write(token)


def load_access_token():
    try:
        with open(ACCESS_TOKEN_FILE, "r") as f:
            return f.read().strip()
    except:
        return None


# -----------------------------------------------------------
# Generate Session Token (Only Once Per Day)
# -----------------------------------------------------------
def generate_access_token():
    if REQUEST_TOKEN is None:
        raise Exception("🚨 REQUEST_TOKEN is not set. Login via browser to obtain it.")

    kite = KiteConnect(api_key=API_KEY)
    redirect_url = kite.login_url()
    data = kite.generate_session(REQUEST_TOKEN, api_secret=API_SECRET)

    access_token = data["access_token"]
    save_access_token(access_token)
    print("✅ Access token generated and saved.")
    return access_token


# -----------------------------------------------------------
# Initialize Kite Object
# -----------------------------------------------------------
def init_kite():
    token = load_access_token()
    kite = KiteConnect(api_key=API_KEY)

    if token:
        try:
            kite.set_access_token(token)
            kite.profile()   # validate
            print("✅ Access Token Loaded")
        except Exception:
            print("⚠️ Access token expired, generating new one…")
            token = generate_access_token()
            kite.set_access_token(token)
    else:
        token = generate_access_token()
        kite.set_access_token(token)

    return kite


# -----------------------------------------------------------
# Fetch Live Ticks Using WebSocket
# -----------------------------------------------------------
def start_websocket(api_key, access_token, instrument_tokens):

    kws = KiteTicker(api_key, access_token)

    def on_ticks(ws, ticks):
        print("📈 Tick:", ticks)

    def on_connect(ws, response):
        print("🔌 Connected to Live Feed")
        ws.subscribe(instrument_tokens)
        ws.set_mode(ws.MODE_FULL, instrument_tokens)

    def on_close(ws, code, reason):
        print("❌ Connection closed", code, reason)

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close

    kws.connect(threaded=True)
    return kws


# -----------------------------------------------------------
# Main Script
# -----------------------------------------------------------
if __name__ == "__main__":

    kite = init_kite()

    # -----------------------------------------------
    # 1. Get All Instruments (Equity + F&O)
    # -----------------------------------------------
    print("📦 Downloading instrument list…")
    instruments = kite.instruments()

    # Example: Find NIFTY Futures (Current Month)
    nf = [
        i for i in instruments
        if i['tradingsymbol'].startswith("NIFTY") and i['segment'] == "NFO-FUT"
    ]
    print("Example NIFTY FUT:", nf[:1])

    # -----------------------------------------------
    # 2. Fetch LTP
    # -----------------------------------------------
    ltp = kite.ltp("NSE:INFY")
    print("🔹 INFY LTP:", ltp)

    # -----------------------------------------------
    # 3. Fetch Historical Data (Candles)
    # -----------------------------------------------
    from datetime import datetime, timedelta

    today = datetime.now()
    from_date = today - timedelta(days=10)

    candles = kite.historical_data(
        instrument_token=408065,   # INFY token
        from_date=from_date,
        to_date=today,
        interval="5minute"
    )
    print("📊 Sample candles:", candles[:3])

    # -----------------------------------------------
    # 4. Place Order (Example)
    # -----------------------------------------------
    # try:
    #     order_id = kite.place_order(Thats great
    #         tradingsymbol="INFY",
    #         exchange=kite.EXCHANGE_NSE,
    #         transaction_type=kite.TRANSACTION_TYPE_BUY,
    #         quantity=1,
    #         order_type=kite.ORDER_TYPE_MARKET,
    #         product=kite.PRODUCT_MIS
    #     )
    #     print("🛒 Order placed:", order_id)
    # except Exception as e:
    #     print("⚠️ Order error:", e)

    # -----------------------------------------------
    # 5. Start Live Ticks
    # -----------------------------------------------
    # Subscribe to INFY + NIFTY FUT (example)
    tokens = [408065, nf[0]["instrument_token"]]
    kws = start_websocket(API_KEY, load_access_token(), tokens)

    # Keep script alive
    while True:
        time.sleep(1)
