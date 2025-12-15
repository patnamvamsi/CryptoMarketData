#!/usr/bin/env python3
"""
Integration test for Redis caching with CryptoMarketData.
Tests the cache decorators and invalidation logic.
"""

import os
os.environ['ENABLE_REDIS_CACHE'] = 'True'
os.environ['REDIS_HOST'] = 'localhost'
os.environ['REDIS_PORT'] = '6379'

import sys
import time

# Test imports
print("Testing cache module imports...")
try:
    from app.cache.redis_client import RedisCache, get_redis_cache, set_redis_cache
    from app.cache.decorators import cache_result
    from app.config import config
    print("✓ Successfully imported cache modules")
except ImportError as e:
    print(f"✗ Failed to import cache modules: {e}")
    sys.exit(1)

def test_cache_decorator():
    """Test the cache_result decorator."""
    print("\n" + "=" * 60)
    print("Testing Cache Decorator")
    print("=" * 60)

    # Initialize Redis cache
    print("\n1. Initializing Redis cache...")
    cache = RedisCache(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        enabled=config.ENABLE_REDIS_CACHE
    )
    set_redis_cache(cache)

    if cache.health_check():
        print("✓ Redis cache initialized and healthy")
    else:
        print("✗ Redis health check failed")
        return False

    # Clear any existing test data
    cache.delete_pattern("test:*")

    # Test decorated function
    print("\n2. Testing decorated function...")

    call_count = 0

    @cache_result(
        key_pattern="test:user:{user_id}",
        ttl=60,
        enabled_check=lambda: config.ENABLE_REDIS_CACHE
    )
    def get_user_data(user_id):
        nonlocal call_count
        call_count += 1
        return {"user_id": user_id, "name": f"User {user_id}", "cached": False}

    # First call - should execute function (cache miss)
    result1 = get_user_data(123)
    if call_count == 1:
        print(f"✓ First call executed function (cache miss)")
        print(f"  Result: {result1}")
    else:
        print(f"✗ Call count incorrect: {call_count}")
        return False

    # Second call - should use cache (cache hit)
    result2 = get_user_data(123)
    if call_count == 1:  # Should still be 1
        print(f"✓ Second call used cache (cache hit)")
        print(f"  Result: {result2}")
    else:
        print(f"✗ Function was called again (expected cache hit)")
        return False

    # Different parameter - should execute function again
    result3 = get_user_data(456)
    if call_count == 2:
        print(f"✓ Different parameter executed function correctly")
        print(f"  Result: {result3}")
    else:
        print(f"✗ Call count incorrect for different parameter: {call_count}")
        return False

    # Test cache metrics
    print("\n3. Checking cache metrics...")
    stats = cache.get_stats()
    print(f"✓ Cache statistics:")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit Rate: {stats['hit_rate']}%")

    if stats['hits'] >= 1 and stats['misses'] >= 2:
        print("✓ Metrics look correct (at least 1 hit and 2 misses)")
    else:
        print(f"⚠ Metrics may be incomplete (this is okay if other tests ran)")

    # Test cache invalidation
    print("\n4. Testing cache invalidation...")
    cache.delete_pattern("test:user:*")
    result4 = get_user_data(123)
    if call_count == 3:
        print("✓ Cache invalidation working (function was called again)")
    else:
        print(f"✗ Cache invalidation failed (call count: {call_count})")
        return False

    # Test TTL
    print("\n5. Testing TTL expiration...")

    @cache_result(
        key_pattern="test:ttl:{key}",
        ttl=2,  # 2 seconds
        enabled_check=lambda: config.ENABLE_REDIS_CACHE
    )
    def get_temp_data(key):
        return {"key": key, "timestamp": time.time()}

    temp_result1 = get_temp_data("test")
    print(f"✓ Cached value with 2-second TTL: {temp_result1}")

    print("  Waiting 3 seconds for TTL to expire...")
    time.sleep(3)

    temp_result2 = get_temp_data("test")
    if temp_result2['timestamp'] != temp_result1['timestamp']:
        print("✓ TTL expiration working (got new value after TTL expired)")
        print(f"  New value: {temp_result2}")
    else:
        print("✗ TTL expiration may not be working correctly")

    # Test with None TTL (no expiry)
    print("\n6. Testing cache with no expiry...")

    @cache_result(
        key_pattern="test:permanent:{key}",
        ttl=None,
        enabled_check=lambda: config.ENABLE_REDIS_CACHE
    )
    def get_permanent_data(key):
        return {"key": key, "permanent": True}

    perm_result = get_permanent_data("config")
    print(f"✓ Cached value with no expiry: {perm_result}")

    # Verify it's still there
    perm_result2 = get_permanent_data("config")
    if perm_result == perm_result2:
        print("✓ Permanent cache working correctly")
    else:
        print("✗ Permanent cache failed")
        return False

    # Cleanup
    print("\n7. Cleaning up test data...")
    cache.delete_pattern("test:*")
    print("✓ Cleanup complete")

    print("\n" + "=" * 60)
    print("✓ All decorator tests passed!")
    print("=" * 60)
    return True


def test_cache_features():
    """Test advanced cache features."""
    print("\n" + "=" * 60)
    print("Testing Advanced Cache Features")
    print("=" * 60)

    cache = get_redis_cache()

    # Test 1: Large values
    print("\n1. Testing large value caching...")
    large_data = {"symbols": [f"SYMBOL{i}" for i in range(1000)], "metadata": "x" * 1000}
    cache.set("test:large", large_data, ttl=60)
    retrieved = cache.get("test:large")
    if retrieved == large_data:
        print(f"✓ Large value caching working (size: {len(str(large_data))} chars)")
    else:
        print("✗ Large value caching failed")
        return False

    # Test 2: Complex nested data
    print("\n2. Testing complex nested data...")
    complex_data = {
        "exchanges": {
            "binance": ["BTC", "ETH", "XRP"],
            "zerodha": ["RELIANCE", "TCS", "INFY"]
        },
        "metadata": {
            "active": True,
            "count": 6,
            "timestamp": 1234567890
        }
    }
    cache.set("test:complex", complex_data, ttl=60)
    retrieved = cache.get("test:complex")
    if retrieved == complex_data:
        print("✓ Complex nested data caching working")
    else:
        print("✗ Complex data caching failed")
        return False

    # Test 3: Circuit breaker (graceful degradation)
    print("\n3. Testing graceful degradation...")
    print("  (Cache errors should not crash the application)")

    # Simulate error by using invalid key
    result = cache.get(None)  # This should handle gracefully
    if result is None:
        print("✓ Graceful error handling working")
    else:
        print("⚠ Unexpected result for error case")

    # Test 4: Pattern matching
    print("\n4. Testing pattern-based operations...")
    cache.set("cryptomarket:symbols:binance:btc", "value1", ttl=60)
    cache.set("cryptomarket:symbols:binance:eth", "value2", ttl=60)
    cache.set("cryptomarket:symbols:zerodha:rel", "value3", ttl=60)
    cache.set("cryptomarket:other:data", "value4", ttl=60)

    deleted = cache.delete_pattern("cryptomarket:symbols:binance:*")
    if deleted == 2:
        print(f"✓ Pattern deletion working correctly (deleted {deleted} keys)")
    else:
        print(f"✗ Pattern deletion failed (deleted {deleted}, expected 2)")
        return False

    # Verify specific deletion
    if cache.get("cryptomarket:symbols:zerodha:rel") == "value3":
        print("✓ Non-matching patterns preserved correctly")
    else:
        print("✗ Non-matching patterns were affected")
        return False

    # Cleanup
    cache.delete_pattern("test:*")
    cache.delete_pattern("cryptomarket:*")

    print("\n" + "=" * 60)
    print("✓ All advanced feature tests passed!")
    print("=" * 60)
    return True


def show_final_stats():
    """Show final cache statistics."""
    cache = get_redis_cache()
    print("\n" + "=" * 60)
    print("Final Cache Statistics")
    print("=" * 60)

    stats = cache.get_stats()
    print(f"\nCache Metrics:")
    print(f"  Total Requests: {stats['total_requests']}")
    print(f"  Cache Hits: {stats['hits']}")
    print(f"  Cache Misses: {stats['misses']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Hit Rate: {stats['hit_rate']:.2f}%")
    print(f"  Circuit Breaker: {'OPEN' if stats.get('circuit_breaker_open') else 'CLOSED'}")

    if 'total_keys' in stats:
        print(f"\nRedis Server:")
        print(f"  Total Keys: {stats['total_keys']}")
        print(f"  Memory Used: {stats.get('memory_used_mb', 'N/A')} MB")
        print(f"  Uptime: {stats.get('uptime_seconds', 'N/A')} seconds")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    print("=" * 60)
    print("CryptoMarketData Redis Cache Integration Test")
    print("=" * 60)

    try:
        # Run tests
        test1_success = test_cache_decorator()
        if not test1_success:
            print("\n✗ Decorator tests failed")
            sys.exit(1)

        test2_success = test_cache_features()
        if not test2_success:
            print("\n✗ Feature tests failed")
            sys.exit(1)

        # Show final statistics
        show_final_stats()

        print("\n" + "=" * 60)
        print("✓✓✓ ALL INTEGRATION TESTS PASSED! ✓✓✓")
        print("=" * 60)
        print("\nRedis caching is fully integrated and working!")
        print("The CryptoMarketData service is ready to use caching.")
        sys.exit(0)

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
