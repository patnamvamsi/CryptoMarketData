# CryptoMarketData - Project Structure

**Last Updated**: 2025-12-15

---

## Directory Organization

```
CryptoMarketData/
├── app/                          # Main application code
│   ├── cache/                    # Redis cache implementation
│   │   ├── redis_client.py       # Redis connection and operations
│   │   └── decorators.py         # Cache decorators
│   ├── config/                   # Configuration management
│   │   └── config.py             # Environment variables & settings
│   ├── db/                       # Database layers
│   │   ├── timescaledb/          # TimescaleDB integration
│   │   │   ├── crud.py           # Database operations
│   │   │   ├── timescaledb_connect.py
│   │   │   └── sql_scripts/      # SQL setup scripts
│   │   └── kdb/                  # KDB+ support (WIP)
│   ├── exchanges/                # Exchange adapters
│   │   ├── base_exchange.py      # Abstract base class
│   │   ├── binance_adapter.py    # Binance implementation
│   │   ├── zerodha_adapter.py    # Zerodha/NSE implementation
│   │   └── exchange_factory.py   # Factory pattern
│   ├── ingest/                   # Data ingestion modules
│   │   ├── historical_data_to_db.py
│   │   ├── zerodha_historical_data.py
│   │   ├── manage_binance_symbols.py
│   │   └── manage_zerodha_symbols.py
│   ├── kafka/                    # Kafka integration
│   │   └── kafka_utils.py
│   ├── models/                   # Pydantic data models
│   │   └── market_data.py        # Symbol, Kline models
│   ├── stream/                   # Real-time streaming
│   │   ├── get_streaming_kline.py  # Binance WebSocket
│   │   └── stream_zerodha_kline.py # Zerodha WebSocket
│   ├── logs/                     # Application logs (gitignored)
│   ├── main.py                   # FastAPI application entry
│   ├── logger.py                 # Logging configuration
│   ├── initialise_db.py          # Database initialization
│   └── zerodha_access_token.txt  # Zerodha auth token (gitignored)
│
├── docs/                         # 📚 Documentation
│   ├── QUICK_START.md            # Daily operations guide
│   ├── CLAUDE.md                 # AI assistant guidance
│   ├── README.md                 # Project README
│   ├── REQUIREMENTS.md           # Feature requirements
│   ├── SYSTEM_TEST_RESULTS.md    # Latest test results
│   ├── REDIS_CACHE_TEST_RESULTS.md
│   ├── ZERODHA_SETUP.md          # Zerodha configuration
│   └── ZERODHA_TABLE_FIX.md      # Database schema fixes
│
├── scripts/                      # 🔧 Utility scripts
│   └── zerodha_auth.py           # Zerodha token generator
│
├── tests/                        # 🧪 Test suites
│   ├── cache/                    # Cache-related tests
│   │   ├── test_cache.py
│   │   └── test_redis_simple.py
│   └── integration/              # Integration tests
│       └── test_cache_integration.py
│
├── logs/                         # Runtime logs (gitignored)
│   ├── app.log                   # Current application log
│   └── server_*.log              # Archived server logs
│
├── .env                          # Environment variables (gitignored)
├── .env.example                  # Environment template
├── .gitignore                    # Git ignore rules
├── docker-compose.yml            # Docker services (Redis)
├── Dockerfile                    # Application container
├── requirements.txt              # Python dependencies
├── start.sh                      # 🚀 Start all services
├── stop.sh                       # 🛑 Stop all services
├── LICENSE                       # MIT License
└── PROJECT_STRUCTURE.md          # This file
```

---

## Key Files

### Entry Points
- **`start.sh`** - One-command startup for all services
- **`stop.sh`** - Gracefully stop all services
- **`app/main.py`** - FastAPI application entry point

### Configuration
- **`.env`** - Environment variables (create from `.env.example`)
- **`app/config/config.py`** - Configuration loader
- **`docker-compose.yml`** - Redis service definition

### Documentation
- **`docs/QUICK_START.md`** - Start here for daily usage
- **`docs/CLAUDE.md`** - Development guidelines
- **`docs/SYSTEM_TEST_RESULTS.md`** - Latest test results

### Scripts
- **`scripts/zerodha_auth.py`** - Generate Zerodha access token (24hr validity)

---

## Quick Navigation

### Starting the System
```bash
./start.sh
```
See: `docs/QUICK_START.md`

### Development
```bash
# Run locally
python -m uvicorn app.main:app --reload --port 8002

# Run tests
python -m pytest tests/

# Generate Zerodha token
python scripts/zerodha_auth.py
```

### API Documentation
- **Swagger UI**: http://localhost:8002/docs
- **ReDoc**: http://localhost:8002/redoc

### Cache Management
- **Stats**: http://localhost:8002/cache/stats
- **Clear All**: `curl -X DELETE http://localhost:8002/cache/clear`
- **Clear Pattern**: `curl -X DELETE http://localhost:8002/cache/clear/{pattern}`

---

## Architecture Overview

### Multi-Exchange Support
The system supports multiple exchanges through a unified adapter pattern:

1. **Binance** - Cryptocurrency markets
2. **Zerodha** - Indian stock markets (NSE, NFO, BSE)

### Data Flow
```
Exchange APIs → Adapters → Ingestion → TimescaleDB
                                    ↓
                              Redis Cache ← FastAPI ← Client
                                    ↓
                              WebSocket Streaming
```

### Caching Strategy
- **Redis** for high-performance caching
- **Cache-Aside** pattern with lazy loading
- **TTL-based** expiration (table checks: permanent, timestamps: 2min, symbols: 5min)
- **Pattern-based** invalidation

---

## Dependencies

### Core
- **FastAPI** - Web framework
- **SQLAlchemy** - Database ORM
- **Redis** - Caching layer
- **Pandas** - Data processing

### Exchange APIs
- **python-binance** - Binance API client
- **kiteconnect** - Zerodha API client

### Database
- **psycopg2** - PostgreSQL adapter
- **TimescaleDB** - Time-series database

### Streaming
- **websockets** - WebSocket support
- **APScheduler** - Background jobs

---

## Environment Variables

Key variables (see `.env.example` for full list):

### Database
- `TIMESCALE_HOST`, `TIMESCALE_PORT`, `TIMESCALE_USERNAME`, `TIMESCALE_PASSWORD`
- `TIMESCALE_MARKET_DATA_DB`

### Binance
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`

### Zerodha
- `ZERODHA_API_KEY`, `ZERODHA_SECRET_KEY`

### Redis Cache
- `ENABLE_REDIS_CACHE=true`
- `REDIS_HOST=localhost`, `REDIS_PORT=6379`

### Features
- `STREAM_MARKET_DATA_KAFKA` - Enable Kafka streaming
- `ENABLE_ZERODHA_STREAMING` - Enable Zerodha WebSocket
- `ENABLE_ZERODHA_GAP_FILL` - Enable automatic gap filling

---

## Development Workflow

### 1. Initial Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env  # Add your API keys

# Initialize database
python app/initialise_db.py
```

### 2. Daily Development
```bash
# Start services
./start.sh

# Monitor logs
tail -f logs/app.log

# Run tests (if needed)
python -m pytest tests/
```

### 3. Making Changes
```bash
# Application auto-reloads when files change
# Test your changes via Swagger UI: http://localhost:8002/docs
```

### 4. Cleanup
```bash
./stop.sh
```

---

## Testing

### Test Organization
- `tests/cache/` - Redis cache tests
- `tests/integration/` - Full integration tests

### Running Tests
```bash
# All tests
python -m pytest tests/

# Specific test file
python tests/cache/test_redis_simple.py

# With coverage
python -m pytest --cov=app tests/
```

---

## Troubleshooting

### Common Issues

**Redis connection failed**
```bash
DOCKER_HOST=unix:///var/run/docker.sock docker ps | grep redis
DOCKER_HOST=unix:///var/run/docker.sock docker restart cryptomarket-redis
```

**Database connection refused**
```bash
psql -h 192.168.0.189 -U postgres -d market_data_dev1 -c "SELECT 1"
```

**Zerodha token expired**
```bash
python scripts/zerodha_auth.py
```

**Port 8002 in use**
```bash
lsof -ti:8002 | xargs kill -9
```

See `docs/QUICK_START.md` for detailed troubleshooting.

---

## Production Deployment

### Using Docker
```bash
# Build image
docker build -t cryptomarketdata:latest .

# Run with docker-compose
docker-compose up -d
```

### Manual Deployment
```bash
# Use production server (e.g., gunicorn)
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8002
```

### Monitoring
- Application logs: `logs/app.log`
- Cache statistics: `/cache/stats`
- Health check: `/`

---

## Contributing

1. Create feature branch
2. Make changes
3. Test thoroughly
4. Update documentation
5. Submit pull request

---

## License

MIT License - See `LICENSE` file

---

## Support

- **Documentation**: `docs/` directory
- **Quick Start**: `docs/QUICK_START.md`
- **Test Results**: `docs/SYSTEM_TEST_RESULTS.md`

---

**Last Updated**: 2025-12-15
**Version**: 1.0 (with Redis caching)
