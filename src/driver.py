"""
src/driver.py — Chrome WebDriver creation and page-wait helpers.
"""

import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def create_driver(profile_dir: str = None, worker_id: int = 0, headless: bool = False):
    """
    Create a Chrome WebDriver instance.

    Parameters
    ----------
    profile_dir : str
        Base chrome profile directory. Each worker gets its own sub-profile.
    worker_id : int
        Worker index (0 = main orchestrator, 1..N = scraping workers).
    headless : bool
        Run without a visible browser window.
    """
    if profile_dir is None:
        from config import CHROME_PROFILE_DIR
        profile_dir = CHROME_PROFILE_DIR

    # Each worker gets its own Chrome profile to avoid session conflicts
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
