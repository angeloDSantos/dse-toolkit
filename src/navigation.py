"""
src/navigation.py — Salesforce page navigation helpers.

Handles: list scrolling, record URL collection, clicking Related tab,
finding / clicking the Orders related list, navigating to POC contacts.
"""

import os
import re
import json
import time
from collections import Counter

from src.driver import settle, base_url
from src.js_scripts import (
    SCROLL_ALL_JS, GET_SCROLL_HEIGHT_JS,
    HARVEST_LINKS_WITH_TEXT_JS, HARVEST_SHADOW_JS, HARVEST_FLAT_JS, HARVEST_TEXT_JS,
    CLICK_RELATED_JS, FIND_ORDERS_JS, FIND_POC_JS, GET_RECORD_NAME_JS, PAGE_TEXT_JS,
)
from src.signals import Controls
from config import (
    SCROLL_AMOUNT, SCROLL_PAUSE, PLATEAU_STEPS, MAX_SCROLL_STEPS,
    SAVE_EVERY, NON_ORDER_PREFIXES,
)


# ─── Record ID parsing ──────────────────────────────────────────────────────

_RECORD_ID_RE = re.compile(r"/lightning/r/([a-zA-Z0-9]{3,18})/view")


def _absolutize(base: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return base + href
    return base + "/" + href


# ─── Scroll and collect list records ─────────────────────────────────────────

def collect_list_records(driver, ctrl: Controls, entity_name: str,
                         list_limit: int = None) -> list:
    """
    Scroll through a Salesforce list view and collect all record URLs + names.
    Returns [(url, name), ...].
    """
    print(f"\n  Starting list scroll for {entity_name}...")
    base = base_url(driver)
    seen_urls = set()
    records   = []
    plateau   = 0

    for step in range(1, MAX_SCROLL_STEPS + 1):
        ctrl.poll()
        ctrl.wait_if_paused()

        try:
            raw = driver.execute_script(HARVEST_LINKS_WITH_TEXT_JS) or []
        except Exception:
            raw = []

        new_count = 0
        for item in raw:
            href = item.get("href", "")
            text = item.get("text", "")
            full = _absolutize(base, href)
            if full and full not in seen_urls:
                seen_urls.add(full)
                records.append((full, text))
                new_count += 1

        if new_count:
            plateau = 0
        else:
            plateau += 1

        if plateau >= PLATEAU_STEPS:
            print(f"  Scroll plateau at step {step} — {len(records)} records found.")
            break

        if list_limit and len(records) >= list_limit:
            print(f"  Hit list limit ({list_limit}) — {len(records)} records found.")
            break

        try:
            driver.execute_script(SCROLL_ALL_JS, SCROLL_AMOUNT)
        except Exception:
            pass
        time.sleep(SCROLL_PAUSE)

    print(f"  Collected {len(records)} records from {entity_name} list.")
    return records


# ─── Click Related tab ──────────────────────────────────────────────────────

def click_related_tab(driver, ctrl: Controls, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        ctrl.poll()
        try:
            if driver.execute_script(CLICK_RELATED_JS):
                settle(driver, 1.0, ctrl)
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ─── Navigate to Orders page ────────────────────────────────────────────────

def navigate_to_orders_page(driver, ctrl: Controls, timeout: float = 10.0,
                             record_url: str = "") -> bool:
    end = time.time() + timeout
    base = base_url(driver)
    while time.time() < end:
        ctrl.poll()
        try:
            result = driver.execute_script(FIND_ORDERS_JS)
            if result and result.get("href"):
                href = result["href"]
                full = _absolutize(base, href)
                driver.get(full)
                settle(driver, 1.0, ctrl)
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ─── Collect order URLs from an orders page ──────────────────────────────────

def collect_order_urls(driver, ctrl: Controls) -> list:
    """
    Auto-detect the order prefix and scroll-collect all order URLs.
    """
    base = base_url(driver)

    # Scan all links and detect the most common non-Contact/Account prefix
    all_urls  = _scan_all_urls(driver, base)
    prefix    = _detect_order_prefix(all_urls)
    if not prefix:
        return []

    seen    = set()
    results = []
    plateau = 0

    for _ in range(1, MAX_SCROLL_STEPS + 1):
        ctrl.poll()
        ctrl.wait_if_paused()
        urls = _harvest_prefix(driver, base, prefix)
        new_count = 0
        for u in urls:
            if u not in seen:
                seen.add(u)
                results.append(u)
                new_count += 1
        if new_count:
            plateau = 0
        else:
            plateau += 1
        if plateau >= PLATEAU_STEPS:
            break
        try:
            driver.execute_script(SCROLL_ALL_JS, SCROLL_AMOUNT)
        except Exception:
            pass
        time.sleep(SCROLL_PAUSE)

    return results


def _scan_all_urls(driver, base):
    found = set()
    for js in (HARVEST_SHADOW_JS, HARVEST_FLAT_JS, HARVEST_TEXT_JS):
        try:
            for h in (driver.execute_script(js) or []):
                found.add(_absolutize(base, str(h).strip()))
        except Exception:
            pass
    return list(found)


def _detect_order_prefix(urls: list) -> str:
    prefix_counts = Counter()
    for url in urls:
        m = _RECORD_ID_RE.search(url)
        if m:
            pfx = m.group(1)[:3]
            prefix_counts[pfx] += 1
    candidates = {p: c for p, c in prefix_counts.items()
                  if not any(p.startswith(s) for s in NON_ORDER_PREFIXES)}
    if not candidates:
        return ""
    return max(candidates, key=candidates.get)


def _harvest_prefix(driver, base, prefix):
    all_urls = _scan_all_urls(driver, base)
    return [u for u in all_urls
            if (m := _RECORD_ID_RE.search(u)) and m.group(1)[:3] == prefix]


# ─── POC navigation ─────────────────────────────────────────────────────────

def go_to_poc(driver, ctrl: Controls, timeout: float = 7.0) -> bool:
    settle(driver, 0.2, ctrl)
    end = time.time() + timeout
    while time.time() < end:
        ctrl.poll()
        try:
            best = driver.execute_script(FIND_POC_JS)
            if best and best.get("href"):
                base = base_url(driver)
                href = best["href"]
                full = _absolutize(base, href)
                driver.get(full)
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def wait_for_contact_ready(driver, ctrl: Controls,
                           timeout: float = 22.0) -> tuple:
    from src.parsing import contact_page_ready, looks_like_interruption
    end = time.time() + timeout
    while time.time() < end:
        ctrl.poll()
        ctrl.wait_if_paused()
        try:
            text = driver.execute_script(PAGE_TEXT_JS) or ""
        except Exception:
            text = ""
        if contact_page_ready(text):
            return True, ""
        if looks_like_interruption(text):
            return False, "interruption"
        time.sleep(0.25)
    return False, "timeout"


def get_page_text(driver) -> str:
    try:
        return driver.execute_script(PAGE_TEXT_JS) or ""
    except Exception:
        return ""


def get_record_name(driver) -> str:
    try:
        return driver.execute_script(GET_RECORD_NAME_JS) or ""
    except Exception:
        return ""


# ─── Progress save/load ─────────────────────────────────────────────────────

def safe_stem(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\- ]", "", (name or "").strip())
    s = re.sub(r"\s+", "_", s)
    return s[:120] or "unnamed"


def load_order_progress(folder: str, stem: str) -> set:
    path = os.path.join(folder, f".progress_{stem}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return set(json.load(f).get("done", []))
        except Exception:
            pass
    return set()


def save_order_progress(folder: str, stem: str, done: set):
    path = os.path.join(folder, f".progress_{stem}.json")
    tmp  = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"done": sorted(done)}, f)
        os.replace(tmp, path)
    except Exception:
        pass
