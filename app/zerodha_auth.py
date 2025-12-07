#!/usr/bin/env python3
"""
Zerodha Authentication Helper

This script helps generate and manage Zerodha access tokens for the CryptoMarketData application.

Usage:
    python zerodha_auth.py

Steps:
    1. Run this script
    2. Open the displayed login URL in your browser
    3. Login to Zerodha and authorize the app
    4. Copy the request_token from the redirect URL
    5. Paste it when prompted
    6. Access token will be generated and saved
"""

import sys
from app.config import config
from kiteconnect import KiteConnect


def main():
    print("=" * 70)
    print("Zerodha Authentication Helper")
    print("=" * 70)
    print()

    # Check if API keys are configured
    if not config.ZERODHA_API_KEY or not config.ZERODHA_SECRET_KEY:
        print("❌ ERROR: Zerodha API credentials not configured!")
        print()
        print("Please update app/config/config.py with:")
        print("  - ZERODHA_API_KEY")
        print("  - ZERODHA_SECRET_KEY")
        print()
        sys.exit(1)

    print("✅ Zerodha API credentials found")
    print()

    # Initialize KiteConnect
    kite = KiteConnect(api_key=config.ZERODHA_API_KEY)

    # Step 1: Generate login URL
    login_url = kite.login_url()
    print("📍 STEP 1: Open this URL in your browser")
    print("=" * 70)
    print(login_url)
    print("=" * 70)
    print()

    # Step 2: Get request token from user
    print("📍 STEP 2: After logging in, you'll be redirected to a URL like:")
    print("  http://127.0.0.1/?request_token=XXXXX&action=login&status=success")
    print()
    request_token = input("Paste the request_token from the URL: ").strip()

    if not request_token:
        print("❌ No request token provided. Exiting.")
        sys.exit(1)

    print()
    print("🔄 Generating access token...")
    print()

    try:
        # Step 3: Generate session/access token
        data = kite.generate_session(request_token, api_secret=config.ZERODHA_SECRET_KEY)
        access_token = data["access_token"]

        # Save to file
        with open("app/zerodha_access_token.txt", "w") as f:
            f.write(access_token)

        print("✅ SUCCESS! Access token generated and saved.")
        print()
        print("=" * 70)
        print("Access Token:", access_token)
        print("=" * 70)
        print()
        print("📝 Token saved to: zerodha_access_token.txt")
        print()
        print("🚀 You can now use the Zerodha adapter:")
        print()
        print("   from app.exchanges import get_exchange")
        print("   zerodha = get_exchange('zerodha')")
        print()
        print("⏰ Note: This token is valid for 24 hours.")
        print("   You'll need to regenerate it daily using this script.")
        print()

    except Exception as e:
        print(f"❌ ERROR: Failed to generate access token")
        print(f"   {e}")
        print()
        print("Common issues:")
        print("  - Invalid request_token (already used or expired)")
        print("  - Incorrect API_SECRET")
        print("  - Network connectivity issues")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
