#!/usr/bin/env python3
"""
Simple Redis connection test without app dependencies.
"""

import redis
import json

def test_redis():
    print("=" * 60)
    print("Testing Redis Connection")
    print("=" * 60)

    # Connect to Redis
    print("\n1. Connecting to Redis at localhost:6379...")
    try:
        client = redis.Redis(
            host='localhost',
            port=6379,
            db=0,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=2
        )

        # Test connection
        response = client.ping()
        if response:
            print("✓ Redis connected successfully (PING response: PONG)")
        else:
            print("✗ Redis connection failed")
            return False
    except Exception as e:
        print(f"✗ Failed to connect to Redis: {e}")
        return False

    # Test SET/GET
    print("\n2. Testing SET and GET operations...")
    test_data = {"message": "Hello from CryptoMarketData!", "value": 123}
    test_key = "cryptomarket:test:connection"

    try:
        # Set value
        client.set(test_key, json.dumps(test_data), ex=60)  # 60 seconds TTL
        print(f"✓ SET successful: {test_key}")

        # Get value
        retrieved = client.get(test_key)
        retrieved_data = json.loads(retrieved) if retrieved else None

        if retrieved_data == test_data:
            print(f"✓ GET successful: {retrieved_data}")
        else:
            print(f"✗ GET failed - Expected: {test_data}, Got: {retrieved_data}")
            return False
    except Exception as e:
        print(f"✗ SET/GET failed: {e}")
        return False

    # Test DELETE
    print("\n3. Testing DELETE operation...")
    try:
        client.delete(test_key)
        if client.get(test_key) is None:
            print("✓ DELETE successful")
        else:
            print("✗ DELETE failed")
            return False
    except Exception as e:
        print(f"✗ DELETE failed: {e}")
        return False

    # Test pattern-based keys
    print("\n4. Testing pattern-based operations...")
    try:
        # Set multiple keys
        client.set("cryptomarket:active_symbols:binance:true", json.dumps(["BTC", "ETH", "XRP"]))
        client.set("cryptomarket:active_symbols:zerodha:true", json.dumps(["RELIANCE", "TCS"]))
        client.set("cryptomarket:max_timestamp:binance_btcusdt", "1234567890")

        # Get keys matching pattern
        keys = client.keys("cryptomarket:active_symbols:*")
        print(f"✓ Found {len(keys)} keys matching 'cryptomarket:active_symbols:*'")
        for key in keys:
            print(f"  - {key}")

        # Clean up
        if keys:
            client.delete(*keys)
            client.delete("cryptomarket:max_timestamp:binance_btcusdt")
            print("✓ Cleanup successful")
    except Exception as e:
        print(f"✗ Pattern operations failed: {e}")
        return False

    # Get Redis info
    print("\n5. Redis server information...")
    try:
        info = client.info()
        print(f"✓ Redis version: {info.get('redis_version', 'unknown')}")
        print(f"✓ Used memory: {round(info.get('used_memory', 0) / 1024 / 1024, 2)} MB")
        print(f"✓ Connected clients: {info.get('connected_clients', 0)}")
        print(f"✓ Total keys in DB0: {info.get('db0', {}).get('keys', 0)}")
    except Exception as e:
        print(f"✗ Failed to get Redis info: {e}")

    print("\n" + "=" * 60)
    print("✓ All Redis tests passed!")
    print("=" * 60)
    print("\nRedis is ready for CryptoMarketData caching!")
    return True

if __name__ == "__main__":
    import sys
    try:
        success = test_redis()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
