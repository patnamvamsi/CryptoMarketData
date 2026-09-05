#!/usr/bin/env python3
"""
Store Zerodha Access Token in Redis

This script authenticates with Zerodha and stores the access token in Redis
for sharing with the CryptoWebServer and other microservices.

Usage:
    python scripts/store_zerodha_token_redis.py

Requirements:
    - ZERODHA_USERNAME, ZERODHA_PASSWORD, ZERODHA_TOTP_KEY in .env
    - OR provide request_token manually
    - Redis running on localhost:6379
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import config
from app.cache.redis_client import RedisCache, get_redis_cache
from app.auth.token_manager import get_token_manager
from kiteconnect import KiteConnect
from app.exchanges import get_exchange

def main():
    print("=" * 70)
    print("Zerodha Token → Redis Storage Script")
    print("=" * 70)
    print()

    # Check Redis connection
    print("🔍 Checking Redis connection...")
    from app.cache.redis_client import set_redis_cache
    redis_cache = RedisCache(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD,
        enabled=True
    )

    if not redis_cache.enabled or not redis_cache.client:
        print("❌ ERROR: Redis connection failed!")
        print(f"   Host: {config.REDIS_HOST}:{config.REDIS_PORT}")
        print()
        sys.exit(1)

    # Set as global instance so ZerodhaAdapter can use it
    set_redis_cache(redis_cache)

    print(f"✅ Redis connected: {config.REDIS_HOST}:{config.REDIS_PORT}")
    print()

    # Check if we should use automated auth
    use_auto_auth = all([
        config.ZERODHA_USERNAME,
        config.ZERODHA_PASSWORD,
        config.ZERODHA_TOTP_KEY,
        config.ENABLE_ZERODHA_AUTO_AUTH
    ])

    if use_auto_auth:
        print("🤖 Automated authentication enabled")
        print(f"   Username: {config.ZERODHA_USERNAME}")
        print()
        print("🔄 Authenticating with Zerodha...")
        print()

        try:
            # Use the ZerodhaAdapter which handles auth and stores token in Redis
            zerodha = get_exchange('zerodha')
            print("✅ SUCCESS! Zerodha authenticated")
            print()

            # Get token info from Redis to confirm
            token_manager = get_token_manager(redis_cache)
            token_info = token_manager.get_token_info()

            if token_info:
                print("=" * 70)
                print("Token stored in Redis successfully!")
                print("=" * 70)
                print(f"User ID: {token_info.get('user_id')}")
                print(f"Generated at: {token_info.get('generated_at')}")
                print(f"Expires at: {token_info.get('expires_at')}")
                print()
                print("🎉 The CryptoWebServer can now access Zerodha portfolio!")
                print()
            else:
                print("⚠️  Token authenticated but not found in Redis")
                print("   This may indicate a storage issue")

        except Exception as e:
            print(f"❌ ERROR: Automated authentication failed")
            print(f"   {e}")
            print()
            print("Falling back to manual authentication...")
            manual_auth(redis_cache)

    else:
        print("ℹ️  Automated authentication not configured")
        print()
        manual_auth(redis_cache)


def manual_auth(redis_cache):
    """Manual authentication flow."""
    print("📍 Manual Authentication Flow")
    print("=" * 70)
    print()

    # Check if API keys are configured
    if not config.ZERODHA_API_KEY or not config.ZERODHA_SECRET_KEY:
        print("❌ ERROR: Zerodha API credentials not configured!")
        print()
        print("Please set in .env:")
        print("  - ZERODHA_API_KEY")
        print("  - ZERODHA_SECRET_KEY")
        print()
        sys.exit(1)

    kite = KiteConnect(api_key=config.ZERODHA_API_KEY)

    # Step 1: Generate login URL
    login_url = kite.login_url()
    print("STEP 1: Open this URL in your browser")
    print("=" * 70)
    print(login_url)
    print("=" * 70)
    print()

    # Step 2: Get request token from user
    print("STEP 2: After logging in, you'll be redirected to a URL like:")
    print("  http://127.0.0.1/?request_token=XXXXX&action=login&status=success")
    print()
    pasted = input("Paste the request_token (or the full redirect URL): ").strip()

    if not pasted:
        print("❌ No request token provided. Exiting.")
        sys.exit(1)

    if 'request_token=' in pasted:
        from urllib.parse import urlparse, parse_qs
        request_token = parse_qs(urlparse(pasted).query).get('request_token', [''])[0]
    else:
        request_token = pasted

    if not request_token:
        print("❌ Could not find a request_token in the pasted value. Exiting.")
        sys.exit(1)

    print()
    print("🔄 Generating access token...")
    print()

    try:
        # Generate session/access token
        data = kite.generate_session(request_token, api_secret=config.ZERODHA_SECRET_KEY)
        access_token = data["access_token"]

        # Validate token
        kite.set_access_token(access_token)
        profile = kite.profile()
        user_id = profile.get('user_id', profile.get('user_name', 'Unknown'))

        # Store in Redis
        token_manager = get_token_manager(redis_cache)
        success = token_manager.store_token(access_token, user_id, profile)

        if success:
            print("✅ SUCCESS! Token stored in Redis")
            print()
            print("=" * 70)
            print("Access Token:", access_token)
            print("User:", profile.get('user_name', user_id))
            print("=" * 70)
            print()
            print("🎉 The CryptoWebServer can now access Zerodha portfolio!")
            print()
            print("⏰ Note: This token is valid for 24 hours.")
            print("   You'll need to re-run this script daily.")
            print()
        else:
            print("❌ Failed to store token in Redis")
            sys.exit(1)

    except Exception as e:
        print(f"❌ ERROR: Failed to generate/store access token")
        print(f"   {e}")
        print()
        print("Common issues:")
        print("  - Invalid request_token (already used or expired)")
        print("  - Incorrect API_SECRET")
        print("  - Network connectivity issues")
        print("  - Redis connection issues")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
