"""
src/driver.py — Chrome WebDriver creation, auto-login, MFA, and page-wait helpers.
"""

import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ─── Driver creation ────────────────────────────────────────────────────────

def create_driver(profile_dir: str = None, worker_id: int = 0, headless: bool = False):
    """Create a Chrome WebDriver instance with isolated profile per worker."""
    if profile_dir is None:
        from config import CHROME_PROFILE_DIR
        profile_dir = CHROME_PROFILE_DIR

    if worker_id > 0:
        profile_dir = os.path.join(profile_dir, f"worker_{worker_id}")
    os.makedirs(profile_dir, exist_ok=True)

    opts = Options()
    opts.add_argument(f"--user-data-dir={profile_dir}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--remote-debugging-port={9222 + worker_id}")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    if headless:
        opts.add_argument("--headless=new")

    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(1)
    return driver


# ─── Auto-login ─────────────────────────────────────────────────────────────

def auto_login(driver, username: str, password: str, timeout: float = 15.0):
    """Navigate to Salesforce login page, fill credentials, and submit.

    Returns True if the login form was submitted successfully.
    Returns False if already logged in or the login page wasn't detected.
    """
    from config import SF_BASE

    # Try navigating to SF — if already logged in, we'll land on the dashboard
    driver.get(SF_BASE)
    time.sleep(2)

    current = driver.current_url.lower()

    # Already logged in — dashboard or lightning page
    if "lightning" in current or "home" in current:
        print(f"  ✓ Already logged in")
        return True

    # Navigate to login page
    if "login" not in current:
        from config import SF_LOGIN_URL
        driver.get(SF_LOGIN_URL)
        time.sleep(2)

    try:
        # Wait for username field
        email_field = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "username"))
        )
        email_field.clear()
        email_field.send_keys(username)

        pwd_field = driver.find_element(By.ID, "password")
        pwd_field.clear()
        pwd_field.send_keys(password)

        # Click the login button
        login_btn = driver.find_element(By.ID, "Login")
        login_btn.click()

        time.sleep(2)
        return True

    except Exception as e:
        print(f"  ✗ Auto-login failed: {e}")
        return False


def _is_mfa_page(driver) -> bool:
    """Check if we're on an MFA/verification code page."""
    try:
        page_text = driver.page_source.lower()
        mfa_indicators = [
            "verification code",
            "verify your identity",
            "enter code",
            "authenticator",
            "two-factor",
            "multi-factor",
            "mfa",
        ]
        return any(indicator in page_text for indicator in mfa_indicators)
    except Exception:
        return False


def _is_logged_in(driver) -> bool:
    """Check if we're past the login/MFA screens."""
    try:
        url = driver.current_url.lower()
        return "lightning" in url or "/home" in url or "/setup/" in url
    except Exception:
        return False


def prompt_mfa(driver, window_label: str = "Window", timeout: float = 120.0):
    """Detect MFA challenge and prompt terminal for auth code.

    Args:
        driver: Selenium WebDriver instance
        window_label: Label for the prompt (e.g. "Window 1/6")
        timeout: Max seconds to wait for MFA input

    Returns True if MFA was handled (or not needed), False on failure.
    """
    time.sleep(2)

    # Already logged in — no MFA needed
    if _is_logged_in(driver):
        print(f"  ✓ {window_label} — logged in (no MFA required)")
        return True

    # Check for MFA page
    if not _is_mfa_page(driver):
        # Maybe the login itself failed; check for error
        try:
            error = driver.find_element(By.ID, "error")
            print(f"  ✗ {window_label} — login error: {error.text}")
        except Exception:
            print(f"  ? {window_label} — unknown page state, URL: {driver.current_url}")
        return False

    # Prompt for MFA code
    print()
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │   MFA CODE REQUIRED — {window_label:<22s}│")
    print(f"  └─────────────────────────────────────────────┘")
    code = input(f"  Enter auth code for {window_label}: ").strip()

    if not code:
        print(f"  ✗ No code entered — skipping {window_label}")
        return False

    # Try to find and fill the verification code input
    try:
        # Common SF MFA input selectors
        code_input = None
        for selector in [
            (By.ID, "smc"),
            (By.ID, "emc"),
            (By.NAME, "otp"),
            (By.CSS_SELECTOR, "input[type='text']"),
            (By.CSS_SELECTOR, "input[type='tel']"),
            (By.CSS_SELECTOR, "input[type='number']"),
        ]:
            try:
                code_input = driver.find_element(*selector)
                if code_input.is_displayed():
                    break
                code_input = None
            except Exception:
                continue

        if not code_input:
            print(f"  ✗ Could not find MFA input field")
            return False

        code_input.clear()
        code_input.send_keys(code)

        # Find and click verify/submit button
        for btn_text in ["Verify", "Submit", "Log In", "Continue"]:
            try:
                buttons = driver.find_elements(By.XPATH,
                    f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                    f"'abcdefghijklmnopqrstuvwxyz'), '{btn_text.lower()}')] | "
                    f"//input[@type='submit'][contains(translate(@value, "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                    f"'{btn_text.lower()}')]")
                for btn in buttons:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(3)
                        break
            except Exception:
                continue

        # Wait for login to complete
        start = time.time()
        while time.time() - start < 15:
            if _is_logged_in(driver):
                print(f"  ✓ {window_label} — MFA accepted, logged in")
                return True
            time.sleep(1)

        print(f"  ? {window_label} — MFA submitted but may not have completed")
        return True

    except Exception as e:
        print(f"  ✗ MFA entry failed: {e}")
        return False


def submit_mfa_code(driver, code: str, timeout: float = 15.0) -> bool:
    """Submit an MFA code to the current MFA page (called from web UI flow).

    Returns True if login completes after code submission.
    """
    if _is_logged_in(driver):
        return True

    if not _is_mfa_page(driver):
        return False

    try:
        # Find the MFA input field
        code_input = None
        for selector in [
            (By.ID, "smc"),
            (By.ID, "emc"),
            (By.NAME, "otp"),
            (By.CSS_SELECTOR, "input[type='text']"),
            (By.CSS_SELECTOR, "input[type='tel']"),
            (By.CSS_SELECTOR, "input[type='number']"),
        ]:
            try:
                code_input = driver.find_element(*selector)
                if code_input.is_displayed():
                    break
                code_input = None
            except Exception:
                continue

        if not code_input:
            print("  ✗ Could not find MFA input field")
            return False

        code_input.clear()
        code_input.send_keys(code)

        # Click verify button
        for btn_text in ["Verify", "Submit", "Log In", "Continue"]:
            try:
                buttons = driver.find_elements(By.XPATH,
                    f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                    f"'abcdefghijklmnopqrstuvwxyz'), '{btn_text.lower()}')] | "
                    f"//input[@type='submit'][contains(translate(@value, "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                    f"'{btn_text.lower()}')]")
                for btn in buttons:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(3)
                        break
            except Exception:
                continue

        # Wait for login
        start = time.time()
        while time.time() - start < timeout:
            if _is_logged_in(driver):
                print("  ✓ MFA accepted via web UI")
                return True
            time.sleep(1)

        return True

    except Exception as e:
        print(f"  ✗ MFA submission failed: {e}")
        return False


def login_all_workers(drivers: list, username: str, password: str):
    """Login all worker browser windows with sequential MFA.

    Each window gets auto-login, then prompts for MFA one at a time.
    Enforces a minimum 30-second gap between MFA code entries.
    """
    from config import MFA_MIN_GAP

    total = len(drivers)
    last_mfa_time = 0

    print()
    print("=" * 55)
    print("  SALESFORCE LOGIN")
    print("=" * 55)
    print(f"  Logging in {total} browser window(s)...")
    print()

    for i, driver in enumerate(drivers):
        label = f"Window {i + 1}/{total}"
        print(f"  [{label}] Auto-filling credentials...")

        auto_login(driver, username, password)

        # Enforce minimum gap between MFA entries
        if last_mfa_time > 0:
            elapsed = time.time() - last_mfa_time
            remaining = MFA_MIN_GAP - elapsed
            if remaining > 0:
                print(f"  ⏳ Waiting {remaining:.0f}s before next MFA code "
                      f"(one per 30s window)...")
                time.sleep(remaining)

        result = prompt_mfa(driver, label)
        if result:
            last_mfa_time = time.time()

    print()
    print("  ✓ All windows logged in")
    print("=" * 55)
    print()


# ─── Page-wait helpers ──────────────────────────────────────────────────────

def settle(driver, seconds: float = 0.3, ctrl=None):
    """Wait for the page to settle, checking signals if ctrl is provided."""
    end = time.time() + seconds
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            driver.execute_script("return document.readyState")
        except Exception:
            pass
        time.sleep(0.05)


def wait_for_page(driver, ctrl=None, timeout: float = 12.0) -> bool:
    """Wait until document.readyState == 'complete'."""
    end = time.time() + timeout
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            if driver.execute_script("return document.readyState") == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def base_url(driver) -> str:
    """Extract the protocol + domain from the current URL."""
    return "/".join(driver.current_url.split("/")[:3])
