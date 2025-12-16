# Quick Start Guide - CryptoMarketData Service

## Easiest Way to Run (One Command)

```bash
cd /home/vamsi/Dev/Projects/CryptoMarketData
./start.sh
```

That's it! This will:
- ✅ Check Docker is running
- ✅ Start Redis container
- ✅ Start FastAPI application
- ✅ Verify everything is working

---

## Prerequisites (One-Time Setup)

### 1. Database Connection
Make sure TimescaleDB is accessible:
```bash
# Check connection
psql -h 192.168.0.189 -U postgres -d market_data_dev1 -c "SELECT 1"
```

### 2. Zerodha Access Token (if using Zerodha)
Generate a fresh token (valid for 24 hours):
```bash
cd /home/vamsi/Dev/Projects/CryptoMarketData
python scripts/zerodha_auth.py
```
Follow the prompts and paste the request token when asked.

### 3. Environment Variables
The `.env` file is already configured with Redis settings.

---

## Daily Startup

### Option 1: Simple Script (Recommended)
```bash
cd /home/vamsi/Dev/Projects/CryptoMarketData
./start.sh
```

### Option 2: Manual Steps
```bash
# 1. Start Redis
DOCKER_HOST=unix:///var/run/docker.sock docker start cryptomarket-redis

# 2. Start FastAPI
cd /home/vamsi/Dev/Projects/CryptoMarketData
python -m uvicorn app.main:app --reload --port 8002 > logs/app.log 2>&1 &
```

---

## Stop Services

```bash
./stop.sh
```

Or manually:
```bash
pkill -f "uvicorn app.main:app"
docker stop cryptomarket-redis  # Optional
```

---

## Verify Everything is Working

### 1. Check API is responding
```bash
curl http://localhost:8002/
# Should return: "welcome to the crypto market data module"
```

### 2. Check Redis cache
```bash
curl http://localhost:8002/cache/stats | python -m json.tool
```

### 3. View API documentation
Open in browser: http://localhost:8002/docs

### 4. Monitor logs
```bash
tail -f logs/app.log
```

---

## Common Issues & Solutions

### Issue: "Docker is not running"
**Solution:**
```bash
# Check Docker status
systemctl status docker

# If not running, you may need to start it (requires sudo)
sudo systemctl start docker
```

### Issue: "Port 8002 already in use"
**Solution:**
```bash
# Find and kill the process
lsof -ti:8002 | xargs kill -9
```

### Issue: "Zerodha access token expired"
**Solution:**
```bash
# Generate new token (do this daily)
python scripts/zerodha_auth.py
```

### Issue: "Redis connection failed"
**Solution:**
```bash
# Check Redis container
DOCKER_HOST=unix:///var/run/docker.sock docker ps | grep redis

# Restart Redis if needed
DOCKER_HOST=unix:///var/run/docker.sock docker restart cryptomarket-redis
```

---

## Monitoring

### View Application Logs
```bash
tail -f logs/app.log
```

### View Redis Logs
```bash
DOCKER_HOST=unix:///var/run/docker.sock docker logs -f cryptomarket-redis
```

### Check Cache Performance
```bash
curl -s http://localhost:8002/cache/stats | python -m json.tool
```

### Check Active Symbols
```bash
curl -s http://localhost:8002/cache/stats | grep -A 5 "total_keys"
```

---

## Useful Commands

### Clear Cache
```bash
# Clear all cache
curl -X DELETE http://localhost:8002/cache/clear

# Clear specific pattern
curl -X DELETE http://localhost:8002/cache/clear/max_timestamp:*
```

### Activate a Symbol
```bash
# For Zerodha
curl -X POST "http://localhost:8002/zerodha/activatesymbol/RELIANCE/5/True"

# For Binance
curl -X POST "http://localhost:8002/activatesymbol/BTCUSDT/5/True"
```

### Refresh Symbols
```bash
curl "http://localhost:8002/update/symbols"
```

---

## Production Tips

1. **Daily Routine:**
   - Run `./start.sh` in the morning
   - Generate fresh Zerodha token if using NSE data
   - Monitor cache hit rate (aim for >70%)

2. **Performance Monitoring:**
   - Check cache stats every few hours
   - Watch for cache hit rate drops
   - Monitor Redis memory usage

3. **Maintenance:**
   - Review logs daily: `tail -50 logs/app.log`
   - Clear old log files weekly
   - Backup Redis data periodically

---

## File Locations

- **Startup Script**: `./start.sh`
- **Stop Script**: `./stop.sh`
- **Application Logs**: `logs/app.log`
- **Configuration**: `.env`
- **Zerodha Token**: `app/zerodha_access_token.txt`

---

## Auto-Start on System Boot (Optional)

If you want the service to start automatically when your system boots:

```bash
# Create systemd service
sudo nano /etc/systemd/system/cryptomarket.service
```

Add this content:
```ini
[Unit]
Description=CryptoMarketData Service
After=docker.service

[Service]
Type=forking
User=vamsi
WorkingDirectory=/home/vamsi/Dev/Projects/CryptoMarketData
ExecStart=/home/vamsi/Dev/Projects/CryptoMarketData/start.sh
ExecStop=/home/vamsi/Dev/Projects/CryptoMarketData/stop.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable cryptomarket.service
sudo systemctl start cryptomarket.service
```

---

## Summary

**Simplest workflow:**
1. `./start.sh` - Start everything
2. Open http://localhost:8002/docs - Use the API
3. `tail -f logs/app.log` - Monitor (optional)
4. `./stop.sh` - Stop when done

That's it! 🚀
