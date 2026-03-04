# CryptoMarketData - Multi-Exchange Market Data Service

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-7.0+-red.svg)](https://redis.io/)

A high-performance microservice for capturing, storing, and streaming market data from multiple exchanges (Binance cryptocurrency & Zerodha/NSE stock markets) with Redis caching and TimescaleDB persistence.

---

## ✨ Features

- 🔄 **Multi-Exchange Support** - Unified interface for Binance (crypto) and Zerodha (NSE stocks)
- ⚡ **High-Performance Caching** - Redis integration with 90%+ hit rates
- 📊 **Time-Series Storage** - TimescaleDB with automatic partitioning
- 🔴 **Real-Time Streaming** - WebSocket connections for live market data
- 🔧 **Automatic Gap Filling** - Intelligent detection and filling of missing data
- 🎯 **RESTful API** - FastAPI with automatic documentation
- 🐳 **Docker Ready** - Containerized services for easy deployment

---

## 🚀 Quick Start

### One-Command Startup
```bash
git clone <repository>
cd CryptoMarketData
cp .env.example .env  # Configure your API keys
./start.sh
```

Visit:
- **API Documentation**: http://localhost:8002/docs
- **Cache Statistics**: http://localhost:8002/cache/stats

For detailed instructions, see [`docs/QUICK_START.md`](docs/QUICK_START.md)

---

## 📋 Prerequisites

- Python 3.10+
- Docker (for Redis)
- TimescaleDB instance
- API Keys:
  - Binance API credentials (for crypto data)
  - Zerodha Kite API credentials (for NSE data)

---

## 📂 Project Structure

```
CryptoMarketData/
├── app/              # Main application code
│   ├── cache/        # Redis caching layer
│   ├── exchanges/    # Exchange adapters (Binance, Zerodha)
│   ├── db/           # Database integrations (TimescaleDB)
│   ├── ingest/       # Historical data downloaders
│   ├── stream/       # WebSocket streaming
│   └── main.py       # FastAPI application
├── docs/             # Documentation
├── scripts/          # Utility scripts
├── tests/            # Test suites
├── start.sh          # Start all services
└── stop.sh           # Stop all services
```

See [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) for detailed structure.

---

## 🔧 Configuration

### Environment Variables

Create `.env` from `.env.example`:

```bash
# Database
TIMESCALE_HOST=localhost
TIMESCALE_PORT=5432
TIMESCALE_USERNAME=postgres
TIMESCALE_PASSWORD=your_password
TIMESCALE_MARKET_DATA_DB=market_data_dev1

# Binance
BINANCE_API_KEY=your_binance_key
BINANCE_API_SECRET=your_binance_secret

# Zerodha
ZERODHA_API_KEY=your_zerodha_key
ZERODHA_SECRET_KEY=your_zerodha_secret

# Redis Cache
ENABLE_REDIS_CACHE=true
REDIS_HOST=localhost
REDIS_PORT=6379
```

### Zerodha Authentication

Zerodha requires daily token generation:
```bash
python scripts/zerodha_auth.py
```

---

## 📊 Performance

### Cache Performance (Typical)
- **Hit Rate**: 90%+
- **Response Time**: <5ms for cached queries
- **Memory Usage**: <50MB for 10K keys

### Data Ingestion
- **Historical**: 300+ candles/second
- **Streaming**: 14+ symbols (Zerodha), unlimited (Binance)
- **Gap Detection**: Automatic with configurable thresholds

---

## 🌐 API Endpoints

### Health & Monitoring
- `GET /` - Service health check
- `GET /docs` - Interactive API documentation
- `GET /cache/stats` - Cache performance metrics

### Symbol Management
- `GET /update/symbols` - Refresh available symbols
- `POST /zerodha/activatesymbol/{symbol}/{priority}/{state}` - Activate symbol

### Data Operations
- `GET /historicaldata` - Fetch historical data
- `GET /historicalgapdata` - Fill data gaps
- `GET /zerodha/update/marketdata/{symbol}` - Update market data

### Cache Management
- `DELETE /cache/clear` - Clear all cache
- `DELETE /cache/clear/{pattern}` - Clear by pattern

---

## 🏗️ Architecture

### Exchange Adapters Pattern
```python
from app.exchanges import get_exchange

# Binance (Crypto)
binance = get_exchange('binance')
btc_data = binance.get_historical_data(
    symbol='BTCUSDT',
    interval='1m',
    start_time=datetime.now() - timedelta(hours=1)
)

# Zerodha (NSE Stocks)
zerodha = get_exchange('zerodha')
reliance_data = zerodha.get_historical_data(
    symbol='RELIANCE',
    interval='5m',
    exchange='NSE',
    start_time=datetime.now() - timedelta(days=1)
)
```

### Data Flow
```
Exchange APIs → Adapters → Ingestion → TimescaleDB
                                    ↓
                              Redis Cache ← FastAPI ← Clients
                                    ↓
                            WebSocket Streaming
```

---

## 🧪 Testing

```bash
# Run all tests
python -m pytest tests/

# Cache tests
python tests/cache/test_redis_simple.py

# Integration tests
python tests/integration/test_cache_integration.py
```

Latest test results: [`docs/SYSTEM_TEST_RESULTS.md`](docs/SYSTEM_TEST_RESULTS.md)

---

## 📖 Documentation

- **[Quick Start Guide](docs/QUICK_START.md)** - Daily operations
- **[Project Structure](PROJECT_STRUCTURE.md)** - Directory organization
- **[System Test Results](docs/SYSTEM_TEST_RESULTS.md)** - Performance metrics
- **[Zerodha Setup](docs/ZERODHA_SETUP.md)** - Zerodha configuration
- **[Redis Cache](docs/REDIS_CACHE_TEST_RESULTS.md)** - Cache implementation details

---

## 🛠️ Development

### Local Development
```bash
# Start Redis
./start.sh

# Run application with hot reload
python -m uvicorn app.main:app --reload --port 8002

# Monitor logs
tail -f logs/app.log
```

### Docker Development
```bash
# Build image
docker build -t cryptomarketdata:dev .

# Run container
docker run -p 8002:8002 cryptomarketdata:dev
```

---

## 🔍 Troubleshooting

### Redis Issues
```bash
# Check Redis
DOCKER_HOST=unix:///var/run/docker.sock docker ps | grep redis

# Restart Redis
DOCKER_HOST=unix:///var/run/docker.sock docker restart cryptomarket-redis
```

### Database Issues
```bash
# Test connection
psql -h $TIMESCALE_HOST -U postgres -d market_data_dev1 -c "SELECT 1"
```

### Application Issues
```bash
# Check logs
tail -50 logs/app.log

# Kill stuck process
pkill -f "uvicorn app.main:app"
```

See [Quick Start Guide](docs/QUICK_START.md) for more troubleshooting.

---

## 📈 Roadmap

- [ ] Binance Futures support
- [ ] Additional exchanges (Coinbase, Kraken)
- [ ] Advanced charting endpoints
- [ ] Machine learning features
- [ ] Real-time alerts system
- [ ] Prometheus metrics export

---

## 🤝 Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Update documentation
6. Submit a pull request

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **FastAPI** - Modern Python web framework
- **Redis** - High-performance caching
- **TimescaleDB** - Time-series database
- **Binance** - Cryptocurrency data
- **Zerodha Kite** - Indian stock market data

---

## 📧 Support

For questions or issues:
- Check the [documentation](docs/)
- Review [test results](docs/SYSTEM_TEST_RESULTS.md)
- Open an issue on GitHub

---

**Status**: ✅ Production Ready (v1.0 with Redis Caching)

**Last Updated**: 2025-12-15
