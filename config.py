"""
CRM Scraper — Configuration
============================
All tunables in one place. Credentials are read from the local 'env'
file so nothing sensitive is in source control.
"""

import os

# ── Load env file ───────────────────────────────────────────────────────────
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

# ── Salesforce URLs ─────────────────────────────────────────────────────────
SF_BASE          = "https://gdsgroup.my.salesforce.com"
SF_LOGIN_URL     = "https://login.salesforce.com"
SF_EVENTS_URL    = ("https://gdsgroup.lightning.force.com/lightning/o/"
                    "Event__c/list?filterName=AllEvents")
SF_CAMPAIGNS_URL = ("https://gdsgroup.lightning.force.com/lightning/o/"
                    "Campaign/list?filterName=All_Active_Summit_Delegate_Campaigns")

# ── Credentials ─────────────────────────────────────────────────────────────
SF_USERNAME = os.environ.get("SF_EMAIL", "ben.webster@gdsgroup.com")
SF_PASSWORD = os.environ.get("SF_PASSWORD", "Cola999!")

# ── Timing ──────────────────────────────────────────────────────────────────
SCROLL_AMOUNT           = 700     # pixels per scroll step
SCROLL_PAUSE            = 0.5     # seconds between scroll steps
PLATEAU_STEPS           = 5       # consecutive same height before stopping scroll
MAX_SCROLL_STEPS        = 120     # hard cap on scroll iterations
BETWEEN_ORDERS          = 0.08    # seconds between order navigations
SAVE_EVERY              = 10      # flush progress every N orders
CONTACT_READY_TIMEOUT   = 22.0    # seconds to wait for contact page
CONTACT_READY_POLL      = 0.25    # polling interval during contact page wait
RELATED_TAB_TIMEOUT     = 8.0     # seconds to find and click Related tab
ORDERS_LINK_TIMEOUT     = 10.0    # seconds to find the Orders related-list link
POC_TIMEOUT             = 7.0     # seconds to navigate to the POC contact

# ── Parallel ────────────────────────────────────────────────────────────────
DEFAULT_WORKERS      = 1
MAX_WORKERS          = 6
WORKER_STAGGER_DELAY = 2.0   # seconds between launching each worker
MFA_MIN_GAP          = 30.0  # minimum seconds between MFA code entries

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
CHROME_PROFILE_DIR   = os.path.join(BASE_DIR, "chrome_profile")
SIGNAL_FILE          = os.path.join(BASE_DIR, "scraper_signal.txt")
SCRAPES_ROOT         = os.path.join(BASE_DIR, "scrapes")

# ── Salesforce ID prefixes that are NOT orders ──────────────────────────────
NON_ORDER_PREFIXES   = {"003", "001", "006", "00Q", "00T", "00U",
                        "005", "00D", "701", "00v"}

# ── ZMTBE exclusion token ──────────────────────────────────────────────────
ZMTBE_TOKEN          = "zmtbe"
