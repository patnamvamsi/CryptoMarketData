"""
Automated Zerodha Authentication
=================================
Logs in to Zerodha Kite, generates TOTP, obtains request_token,
and exchanges it for an access_token — fully headless, no browser needed.

Uses direct HTTP requests to Zerodha's login endpoints.
"""

import logging
import os
import time
from urllib.parse import urlparse, parse_qs

import pyotp
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logger = logging.getLogger(__name__)


class ZerodhaAuthError(Exception):
    """Raised when automated Zerodha authentication fails."""
    pass


# Zerodha login endpoints
LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"


def get_request_token(
    api_key: str = None,
    username: str = None,
    password: str = None,
    totp_key: str = None,
) -> str:
    """
    Perform automated Zerodha login and return a request_token.

    Steps:
        1. POST credentials to /api/login → get request_id
        2. Generate TOTP from secret key
        3. POST TOTP to /api/twofa → follow redirect to get request_token

    Args:
        api_key: Kite Connect API key (falls back to env)
        username: Zerodha user ID (falls back to env)
        password: Zerodha password (falls back to env)
        totp_key: TOTP secret for 2FA (falls back to env)

    Returns:
        request_token string

    Raises:
        ZerodhaAuthError: If any step fails
    """
    api_key = api_key or os.getenv('ZERODHA_API_KEY')
    username = username or os.getenv('ZERODHA_USERNAME')
    password = password or os.getenv('ZERODHA_PASSWORD')
    totp_key = totp_key or os.getenv('ZERODHA_TOTP_KEY')

    if not all([api_key, username, password, totp_key]):
        raise ZerodhaAuthError(
            "Missing credentials. Need ZERODHA_API_KEY, ZERODHA_USERNAME, "
            "ZERODHA_PASSWORD, ZERODHA_TOTP_KEY in .env or as parameters."
        )

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-Kite-Version': '3',
    })

    # Step 1: Login with username + password
    logger.info(f"Zerodha auto-auth: logging in as {username}")
    try:
        resp = session.post(LOGIN_URL, data={
            'user_id': username,
            'password': password,
        })
        resp.raise_for_status()
        login_data = resp.json()
    except Exception as e:
        raise ZerodhaAuthError(f"Login request failed: {e}")

    if login_data.get('status') != 'success':
        raise ZerodhaAuthError(f"Login failed: {login_data.get('message', login_data)}")

    request_id = login_data['data']['request_id']
    logger.info("Zerodha auto-auth: login OK, proceeding to 2FA")

    # Step 2: Generate TOTP (with optional clock offset correction)
    totp_offset = int(os.getenv('ZERODHA_TOTP_OFFSET', '0'))
    totp = pyotp.TOTP(totp_key)
    totp_value = totp.at(int(time.time()) + totp_offset) if totp_offset else totp.now()

    # Step 3: Submit 2FA (TOTP)
    try:
        resp = session.post(TWOFA_URL, data={
            'user_id': username,
            'request_id': request_id,
            'twofa_value': totp_value,
            'twofa_type': 'totp',
        })
        resp.raise_for_status()
        twofa_data = resp.json()
    except Exception as e:
        raise ZerodhaAuthError(f"2FA request failed: {e}")

    if twofa_data.get('status') != 'success':
        # TOTP might be at boundary — wait and retry once
        logger.warning("2FA failed, retrying with fresh TOTP...")
        time.sleep(2)
        totp_value = totp.at(int(time.time()) + totp_offset) if totp_offset else totp.now()
        try:
            resp = session.post(TWOFA_URL, data={
                'user_id': username,
                'request_id': request_id,
                'twofa_value': totp_value,
                'twofa_type': 'totp',
            })
            resp.raise_for_status()
            twofa_data = resp.json()
        except Exception as e:
            raise ZerodhaAuthError(f"2FA retry failed: {e}")

        if twofa_data.get('status') != 'success':
            raise ZerodhaAuthError(f"2FA failed: {twofa_data.get('message', twofa_data)}")

    logger.info("Zerodha auto-auth: 2FA OK")

    # Step 4: Hit the Kite Connect login URL to get the redirect with request_token
    kite_login_url = (
        f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    )
    try:
        resp = session.get(kite_login_url, allow_redirects=False)
    except Exception as e:
        raise ZerodhaAuthError(f"Kite connect redirect failed: {e}")

    # Follow redirects manually to capture request_token
    redirect_url = resp.headers.get('Location', '')

    # Sometimes need to follow one more redirect
    if redirect_url and 'request_token' not in redirect_url:
        try:
            resp = session.get(redirect_url, allow_redirects=False)
            redirect_url = resp.headers.get('Location', redirect_url)
        except Exception as e:
            raise ZerodhaAuthError(f"Follow redirect failed: {e}")

    # Also check the finish endpoint
    if 'request_token' not in redirect_url:
        finish_url = f"https://kite.zerodha.com/connect/finish?api_key={api_key}&v=3"
        try:
            resp = session.get(finish_url, allow_redirects=False)
            redirect_url = resp.headers.get('Location', redirect_url)
        except Exception as e:
            raise ZerodhaAuthError(f"Finish redirect failed: {e}")

    # Parse request_token from redirect URL
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    if 'request_token' not in params:
        raise ZerodhaAuthError(
            f"Could not extract request_token from redirect. "
            f"URL: {redirect_url}"
        )

    request_token = params['request_token'][0]
    logger.info("Zerodha auto-auth: got request_token successfully")
    return request_token


def get_access_token(
    api_key: str = None,
    api_secret: str = None,
    username: str = None,
    password: str = None,
    totp_key: str = None,
) -> str:
    """
    Full automated auth: login → 2FA → request_token → access_token.

    Returns:
        access_token string (valid ~24 hours)
    """
    from kiteconnect import KiteConnect

    api_key = api_key or os.getenv('ZERODHA_API_KEY')
    api_secret = api_secret or os.getenv('ZERODHA_SECRET_KEY')

    request_token = get_request_token(
        api_key=api_key,
        username=username,
        password=password,
        totp_key=totp_key,
    )

    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data['access_token']

    logger.info("Zerodha auto-auth: access_token generated successfully")
    return access_token


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    token = get_access_token()
    print(f"Access token: {token}")
