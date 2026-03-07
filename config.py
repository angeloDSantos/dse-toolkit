"""
CRM Scraper — Configuration
============================
All tunables in one place. Credentials are read from environment
variables or the 'env' file so nothing sensitive is hardcoded.
"""

import os

# ── Salesforce URLs ──────────────────────────────────────────────────────────
SF_BASE          = "https://gdsgroup.my.salesforce.com"
SF_EVENTS_URL    = ("https://gdsgroup.lightning.force.com/lightning/o/"
                    "Event__c/list?filterName=AllEvents")
SF_CAMPAIGNS_URL = ("https://gdsgroup.lightning.force.com/lightning/o/"
                    "Campaign/list?filterName=All_Active_Summit_Delegate_Campaigns")

# ── Credentials (from env file or environment) ──────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

SF_USERNAME = os.environ.get("SF_EMAIL", "")
SF_PASSWORD = os.environ.get("SF_PASSWORD", "")

# ── Timing ──────────────────────────────────────────────────────────────────
SCROLL_AMOUNT        = 700
SCROLL_PAUSE         = 0.5
PLATEAU_STEPS        = 5
MAX_SCROLL_STEPS     = 120
BETWEEN_ORDERS       = 0.08
SAVE_EVERY           = 10
CONTACT_READY_TIMEOUT = 22.0
CONTACT_READY_POLL   = 0.25

# ── Parallel ────────────────────────────────────────────────────────────────
DEFAULT_WORKERS      = 1
MAX_WORKERS          = 6
WORKER_STAGGER_DELAY = 2.0   # seconds between launching each worker

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
CHROME_PROFILE_DIR   = os.path.join(BASE_DIR, "chrome_profile")
SIGNAL_FILE          = os.path.join(BASE_DIR, "scraper_signal.txt")

# ── Salesforce ID prefixes that are NOT orders ──────────────────────────────
NON_ORDER_PREFIXES   = {"003", "001", "006", "00Q", "00T", "00U", "005", "00D"}

# ── ZMTBE exclusion token ───────────────────────────────────────────────────
ZMTBE_TOKEN          = "zmtbe"
