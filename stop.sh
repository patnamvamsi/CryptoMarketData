#!/bin/bash
# CryptoMarketData Service Stop Script

echo "=========================================="
echo "Stopping CryptoMarketData Services"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# Set Docker host if needed
if ! docker ps &> /dev/null; then
    if DOCKER_HOST=unix:///var/run/docker.sock docker ps &> /dev/null; then
        export DOCKER_HOST=unix:///var/run/docker.sock
    fi
fi

# Stop FastAPI
echo "Stopping FastAPI..."
if pgrep -f "uvicorn app.main:app" > /dev/null; then
    pkill -f "uvicorn app.main:app"
    echo -e "${GREEN}✓ FastAPI stopped${NC}"
else
    echo "FastAPI not running"
fi

# Stop Redis (optional - uncomment to stop Redis too)
# echo ""
# echo "Stopping Redis..."
# if docker ps | grep -q cryptomarket-redis; then
#     docker stop cryptomarket-redis
#     echo -e "${GREEN}✓ Redis stopped${NC}"
# else
#     echo "Redis not running"
# fi

echo ""
echo "=========================================="
echo -e "${GREEN}Services stopped${NC}"
echo "=========================================="
echo ""
echo "Note: Redis container is still running (not stopped by default)"
echo "To stop Redis: docker stop cryptomarket-redis"
echo ""
