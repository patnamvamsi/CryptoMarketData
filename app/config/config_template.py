import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

APP_NAME = os.getenv('APP_NAME', 'VRITTI')

# start_time is considered as 01-01-2010 epoch equivalent  = 1262304000000
START_OF_TIME = int(os.getenv('START_OF_TIME', '1262304000'))  # 01-01-2010

# Binance keys
API_KEY = os.getenv('API_KEY', '')
API_SECRET = os.getenv('API_SECRET', '')
coinbase = os.getenv('COINBASE', '')


# Reddit Keys
REDDIT_URL = os.getenv('REDDIT_URL', 'https://www.reddit.com/prefs/apps')
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', '')
REDDIT_SECRET = os.getenv('REDDIT_SECRET', '')
REDDIT_USERID = os.getenv('REDDIT_USERID', '')
REDDIT_PASWWORD = os.getenv('REDDIT_PASSWORD', '')


# Twitter keys
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY', '')
TWITTER_SECRET_KEY = os.getenv('TWITTER_SECRET_KEY', '')
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN', '')
TWITTER_TOKEN_SECRET = os.getenv('TWITTER_TOKEN_SECRET', '')
TWITTER_APP_NAME = os.getenv('TWITTER_APP_NAME', '')

# DATA_ROOT_DIR = "z:\\crypto\\binance_historical_data\\"
DATA_ROOT_DIR = os.getenv('DATA_ROOT_DIR', '/media/vamsi/Elements 8TB/crypto/binance_historical_data/1day/')

# Timescale login
TIMESCALE_USERNAME = os.getenv('TIMESCALE_USERNAME', 'postgres')
TIMESCALE_PASSWORD = os.getenv('TIMESCALE_PASSWORD', '')
# TIMESCALE_HOST = '192.168.0.101'
TIMESCALE_HOST = os.getenv('TIMESCALE_HOST', 'localhost')
TIMESCALE_PORT = os.getenv('TIMESCALE_PORT', '5432')
TIMESCALE_MARKET_DATA_DB = os.getenv('TIMESCALE_MARKET_DATA_DB', 'market_data')

# kafka config:
STREAM_MARKET_DATA_KAFKA = os.getenv('STREAM_MARKET_DATA_KAFKA', 'False').lower() == 'true'
STREAM_APP_LOG_KAFKA = os.getenv('STREAM_APP_LOG_KAFKA', 'False').lower() == 'true'
KAFKA_BOOTSTRAP_SERVERS = [server.strip() for server in os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')]
KAFKA_MARKET_DATA_TOPIC = os.getenv('KAFKA_MARKET_DATA_TOPIC', 'MARKET_DATA')
KAFKA_APP_LOG_TOPIC = os.getenv('KAFKA_APP_LOG_TOPIC', 'APPLICATION_LOGS')
KAFKA_MARKET_NEWS_TOPIC = os.getenv('KAFKA_MARKET_NEWS_TOPIC', 'MARKET_NEWS')
KAFKA_ORDERS_TOPIC = os.getenv('KAFKA_ORDERS_TOPIC', 'ORDERS')
KAFKA_TRADES_TOPIC = os.getenv('KAFKA_TRADES_TOPIC', 'TRADES')
KAFKA_TOPICS = [KAFKA_MARKET_DATA_TOPIC, KAFKA_APP_LOG_TOPIC, KAFKA_MARKET_NEWS_TOPIC,
                KAFKA_ORDERS_TOPIC, KAFKA_TRADES_TOPIC]


# Zerodha
ZERODHA_API_KEY = os.getenv('ZERODHA_API_KEY', '')
ZERODHA_SECRET_KEY = os.getenv('ZERODHA_SECRET_KEY', '')

# Default NSE symbols to track on startup
DEFAULT_NSE_SYMBOLS = [
    'RELIANCE',
    'TCS',
    'HDFCBANK',
    'INFY',
    'ICICIBANK',
    'HINDUNILVR',
    'SBIN',
    'BHARTIARTL',
    'ITC',
    'KOTAKBANK',
    'LT',
    'AXISBANK',
    'ASIANPAINT',
    'MARUTI',
    'WIPRO'
]

# Zerodha configuration
ZERODHA_EXCHANGE_SEGMENT = os.getenv('ZERODHA_EXCHANGE_SEGMENT', 'NSE')  # NSE, NFO, BSE
ZERODHA_DEFAULT_INTERVAL = os.getenv('ZERODHA_DEFAULT_INTERVAL', '1m')   # 1m, 5m, 15m, 1h, 1d
ENABLE_ZERODHA_STREAMING = os.getenv('ENABLE_ZERODHA_STREAMING', 'True').lower() == 'true'   # Enable/disable Zerodha streaming on startup
ENABLE_ZERODHA_GAP_FILL = os.getenv('ENABLE_ZERODHA_GAP_FILL', 'True').lower() == 'true'    # Enable/disable gap filling on startup

# Zerodha data fetching limits
# Note: Zerodha API has limits on historical data:
# - Max 60 days for minute-level data
# - Max 2000 candles per request
ZERODHA_MAX_DAYS_BACK = int(os.getenv('ZERODHA_MAX_DAYS_BACK', '60'))  # Maximum days to fetch historical data (for initial fetch)
