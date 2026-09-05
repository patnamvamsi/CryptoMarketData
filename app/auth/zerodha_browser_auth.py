"""
Zerodha Browser-Based Automated Authentication
===============================================

Drives the actual Kite Connect login page with a real (headless) Chromium browser via
Playwright, rather than forging raw HTTP requests to Zerodha's internal login endpoints
(see app/auth/zerodha_auto_auth.py, which does the latter and is blocked with a persistent
403 as of 2026-08-22 — see docs/ZERODHA_TOKEN_SHARING.md and
~/vault/CryptoMarketData/zerodha-auth-flow.md for the full history).

This automates the exact page a human already logs into manually every day — same URL,
same form fields, same redirect-based token capture — just driven by a script instead of
a mouse. It is meaningfully different from the raw-requests approach: no internal/private
endpoints are touched, and it should be indistinguishable from a real browser session to
Zerodha's anti-bot layer (real JS execution, real TLS/browser fingerprint, real cookies).

Not guaranteed to work — Zerodha can still change the page's markup or add headless-browser
detection at any time. If this starts failing, check for selector drift first (their form
field IDs/classes), then fall back to the manual routine in
scripts/store_zerodha_token_redis.py, which always works because a human is present.
"""

import logging
import os
import time
from urllib.parse import urlparse, parse_qs

import pyotp

logger = logging.getLogger(__name__)


class ZerodhaBrowserAuthError(Exception):
    """Raised when browser-driven Zerodha authentication fails."""
    pass


LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"

# Screenshots are saved here on failure, for debugging selector drift without needing
# to reproduce the failure interactively.
DEBUG_SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "logs")


def get_request_token(
    api_key: str = None,
    username: str = None,
    password: str = None,
    totp_key: str = None,
    timeout: int = 30,
) -> str:
    """
    Perform automated Zerodha login via a real headless browser and return a request_token.

    Runs the actual Playwright work in a dedicated thread. Playwright's sync API refuses to
    run inside a thread that already has an asyncio event loop (raises "It looks like you are
    using Playwright Sync API inside the asyncio loop") — and this function is called from
    ZerodhaAdapter.__init__(), reached from FastAPI's startup handler, which runs on
    uvicorn's asyncio loop. A plain background thread has no event loop of its own, so this
    sidesteps the conflict without needing to make the whole synchronous call chain
    (get_exchange('zerodha') and everything above it) async.

    Args:
        api_key: Kite Connect API key (falls back to env)
        username: Zerodha user ID (falls back to env)
        password: Zerodha password (falls back to env)
        totp_key: TOTP secret for 2FA (falls back to env)
        timeout: per-action timeout in seconds

    Returns:
        request_token string

    Raises:
        ZerodhaBrowserAuthError: If any step fails
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _get_request_token_sync, api_key, username, password, totp_key, timeout
        )
        return future.result()


def _get_request_token_sync(
    api_key: str,
    username: str,
    password: str,
    totp_key: str,
    timeout: int,
) -> str:
    """The actual Playwright work. Only ever call this from a plain thread (see
    get_request_token above) — never directly from asyncio-loop code."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as e:
        raise ZerodhaBrowserAuthError(f"playwright not installed: {e}")

    api_key = api_key or os.getenv('ZERODHA_API_KEY')
    username = username or os.getenv('ZERODHA_USERNAME')
    password = password or os.getenv('ZERODHA_PASSWORD')
    totp_key = totp_key or os.getenv('ZERODHA_TOTP_KEY')

    if not all([api_key, username, password, totp_key]):
        raise ZerodhaBrowserAuthError(
            "Missing credentials. Need ZERODHA_API_KEY, ZERODHA_USERNAME, "
            "ZERODHA_PASSWORD, ZERODHA_TOTP_KEY in .env or as parameters."
        )

    timeout_ms = timeout * 1000
    login_url = LOGIN_URL_TEMPLATE.format(api_key=api_key)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            logger.info(f"Zerodha browser auto-auth: navigating to login page")
            page.goto(login_url, timeout=timeout_ms)

            # User ID and password are both on this one page (not a multi-step wizard) —
            # fill both before submitting once. Submitting after userid alone triggers
            # "Invalid username or password" with an empty password field.
            page.locator("#userid").wait_for(state="visible", timeout=timeout_ms)
            page.locator("#userid").fill(username)
            page.locator("#password").wait_for(state="visible", timeout=timeout_ms)
            page.locator("#password").fill(password)
            page.locator("#password").press("Enter")

            # Step 3: TOTP — generate right before filling to keep the 30s window fresh
            otp_selector = "form.twofa-form input[type='number'], input[placeholder='Enter OTP'], input[type='text'][maxlength='6']"
            page.locator(otp_selector).first.wait_for(state="visible", timeout=timeout_ms)
            totp_value = pyotp.TOTP(totp_key).now()
            page.locator(otp_selector).first.fill(totp_value)
            page.locator(otp_selector).first.press("Enter")

            # Step 4: Wait for the redirect carrying request_token
            page.wait_for_url("**request_token=**", timeout=timeout_ms)
            redirect_url = page.url

        except PlaywrightTimeoutError as e:
            _save_debug_screenshot(page)
            raise ZerodhaBrowserAuthError(
                f"Timed out waiting for a login step (page markup may have changed): {e}"
            )
        except Exception as e:
            _save_debug_screenshot(page)
            raise ZerodhaBrowserAuthError(f"Browser login failed: {e}")
        finally:
            browser.close()

    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    if 'request_token' not in params:
        raise ZerodhaBrowserAuthError(
            f"Could not extract request_token from redirect. URL: {redirect_url}"
        )

    request_token = params['request_token'][0]
    logger.info("Zerodha browser auto-auth: got request_token successfully")
    return request_token


def _save_debug_screenshot(page) -> None:
    """Best-effort screenshot on failure, for diagnosing selector drift later."""
    try:
        os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(
            DEBUG_SCREENSHOT_DIR, f"zerodha_browser_auth_failure_{int(time.time())}.png"
        )
        page.screenshot(path=path)
        logger.warning(f"Saved failure screenshot to {path}")
    except Exception as e:
        logger.debug(f"Could not save debug screenshot: {e}")


def get_access_token(
    api_key: str = None,
    api_secret: str = None,
    username: str = None,
    password: str = None,
    totp_key: str = None,
) -> str:
    """
    Full browser-driven auth: login → 2FA → request_token → access_token.

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

    logger.info("Zerodha browser auto-auth: access_token generated successfully")
    return access_token


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    token = get_access_token()
    print(f"Access token: {token}")
