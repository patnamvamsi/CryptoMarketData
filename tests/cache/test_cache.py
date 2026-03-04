#!/usr/bin/env python3
"""
Simple test script to verify Redis cache implementation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.cache.redis_client import RedisCache
from app.config import config

def test_redis_connection():
    """Test basic Redis connection."""
    print("=" * 60)
    print("Testing Redis Cache Implementation")
    print("=" * 60)

    # Test 1: Initialize Redis client
    print("\n1. Initializing Redis client...")
    cache = RedisCache(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD,
        enabled=config.ENABLE_REDIS_CACHE
    )

    if cache.health_check():
        print("✓ Redis connection healthy")
    else:
        print("✗ Redis connection failed")
        return False

    # Test 2: Set and Get
    print("\n2. Testing SET and GET operations...")
    test_key = "test:key:1"
    test_value = {"message": "Hello Redis!", "count": 42}

    cache.set(test_key, test_value, ttl=60)
    retrieved = cache.get(test_key)

    if retrieved == test_value:
        print(f"✓ SET/GET working correctly")
        print(f"  Stored: {test_value}")
        print(f"  Retrieved: {retrieved}")
    else:
        print(f"✗ SET/GET failed")
        print(f"  Expected: {test_value}")
        print(f"  Got: {retrieved}")
        return False

    # Test 3: Cache with TTL
    print("\n3. Testing TTL (Time To Live)...")
    cache.set("test:ttl", "temporary", ttl=2)
    if cache.get("test:ttl") == "temporary":
        print("✓ TTL value set correctly")
    else:
        print("✗ TTL set failed")
        return False

    # Test 4: Delete
    print("\n4. Testing DELETE operation...")
    cache.delete(test_key)
    if cache.get(test_key) is None:
        print("✓ DELETE working correctly")
    else:
        print("✗ DELETE failed")
        return False

    # Test 5: Pattern deletion
    print("\n5. Testing pattern-based deletion...")
    cache.set("cryptomarket:test:1", "value1", ttl=60)
    cache.set("cryptomarket:test:2", "value2", ttl=60)
    cache.set("cryptomarket:other:1", "value3", ttl=60)

    deleted = cache.delete_pattern("cryptomarket:test:*")
    if deleted == 2:
        print(f"✓ Pattern deletion working (deleted {deleted} keys)")
    else:
        print(f"✗ Pattern deletion failed (deleted {deleted} keys, expected 2)")
        return False

    # Verify other key still exists
    if cache.get("cryptomarket:other:1") == "value3":
        print("✓ Non-matching keys preserved correctly")
    else:
        print("✗ Non-matching keys affected")
        return False

    # Test 6: Metrics
    print("\n6. Testing metrics tracking...")
    stats = cache.get_stats()
    print(f"✓ Cache statistics:")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Hit Rate: {stats['hit_rate']}%")
    print(f"  Total Keys: {stats.get('total_keys', 'N/A')}")
    print(f"  Memory Used: {stats.get('memory_used_mb', 'N/A')} MB")

    # Clean up
    print("\n7. Cleaning up test data...")
    cache.delete_pattern("cryptomarket:*")
    cache.delete_pattern("test:*")
    print("✓ Cleanup complete")

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    try:
        success = test_redis_connection()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
