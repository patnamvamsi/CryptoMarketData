#!/bin/bash
# CryptoMarketData Service Startup Script
# Simple one-command startup for the entire system

set -e  # Exit on any error

echo "=========================================="
echo "CryptoMarketData Service Startup"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [ ! -f "app/main.py" ]; then
    echo -e "${RED}Error: Must run from CryptoMarketData directory${NC}"
    exit 1
fi

# Step 1: Check Docker daemon
echo -e "${YELLOW}[1/4] Checking Docker...${NC}"
if ! docker ps &> /dev/null; then
    if ! DOCKER_HOST=unix:///var/run/docker.sock docker ps &> /dev/null; then
        echo -e "${RED}✗ Docker is not running${NC}"
        echo "Please start Docker first"
        exit 1
    fi
    export DOCKER_HOST=unix:///var/run/docker.sock
fi
echo -e "${GREEN}✓ Docker is running${NC}"
echo ""

# Step 2: Start Redis
echo -e "${YELLOW}[2/4] Starting Redis...${NC}"
if docker ps | grep -q cryptomarket-redis; then
    echo -e "${GREEN}✓ Redis already running${NC}"
else
    if docker ps -a | grep -q cryptomarket-redis; then
        echo "Starting existing Redis container..."
        docker start cryptomarket-redis
    else
        echo "Creating new Redis container..."
        docker run -d --name cryptomarket-redis \
            -p 6379:6379 \
            -v cryptomarket_redis_data:/data \
            redis:7-alpine \
            redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    fi
    sleep 2
    echo -e "${GREEN}✓ Redis started${NC}"
fi
echo ""

# Step 3: Check Redis connection
echo -e "${YELLOW}[3/4] Testing Redis connection...${NC}"
if docker exec cryptomarket-redis redis-cli ping | grep -q PONG; then
    echo -e "${GREEN}✓ Redis responding${NC}"
else
    echo -e "${RED}✗ Redis not responding${NC}"
    exit 1
fi
echo ""

# Step 4: Start FastAPI
echo -e "${YELLOW}[4/4] Starting FastAPI application...${NC}"

# Kill existing uvicorn process if running
if pgrep -f "uvicorn app.main:app" > /dev/null; then
    echo "Stopping existing FastAPI process..."
    pkill -f "uvicorn app.main:app"
    sleep 2
fi

echo "Starting FastAPI on port 8002..."
nohup python -m uvicorn app.main:app --reload --port 8002 > logs/app.log 2>&1 &
FASTAPI_PID=$!
sleep 5

# Check if FastAPI started successfully
if curl -s http://localhost:8002/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ FastAPI started successfully (PID: $FASTAPI_PID)${NC}"
else
    echo -e "${RED}✗ FastAPI failed to start${NC}"
    echo "Check logs/app.log for errors"
    exit 1
fi
echo ""

# Summary
echo "=========================================="
echo -e "${GREEN}✓ All services started successfully!${NC}"
echo "=========================================="
echo ""
echo "Service Status:"
echo "  • Redis:   http://localhost:6379"
echo "  • API:     http://localhost:8002"
echo "  • Docs:    http://localhost:8002/docs"
echo "  • Cache:   http://localhost:8002/cache/stats"
echo ""
echo "Logs:"
echo "  • Application: tail -f logs/app.log"
echo "  • Redis: docker logs -f cryptomarket-redis"
echo ""
echo "To stop services, run: ./stop.sh"
echo ""
