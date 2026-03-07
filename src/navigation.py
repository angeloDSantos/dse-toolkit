"""
src/navigation.py — List collection, order finding (4-layer), and page navigation (spec §9).
"""

import os
import re
import json
import time
from datetime import datetime

from config import (
    SCROLL_AMOUNT, SCROLL_PAUSE, PLATEAU_STEPS, MAX_SCROLL_STEPS,
    NON_ORDER_PREFIXES, RELATED_TAB_TIMEOUT, ORDERS_LINK_TIMEOUT,
    CONTACT_READY_TIMEOUT, CONTACT_READY_POLL, POC_TIMEOUT,
)
from src.driver import settle, wait_for_page


# ─── Helpers ────────────────────────────────────────────────────────────────

def safe_stem(name: str) -> str:
    """Convert a record name to a filesystem-safe stem."""
    return re.sub(r'[^\w\-]', '_', name).strip('_')[:80]


# ─── List collection with scroll (spec §9.2 Layer 3 pattern) ──────────────

def collect_list_records(driver, ctrl=None, limit: int = 500) -> list:
    """Scroll through a Salesforce list view and collect record links.

    Returns a list of (name, url) tuples.
    """
    wait_for_page(driver, ctrl)
    settle(driver, 1.5, ctrl)

    records = {}  # url -> name
    prev_height = 0
    plateau_count = 0

    for step in range(MAX_SCROLL_STEPS):
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()

        # Harvest links via JS
        try:
            links = driver.execute_script("""
                var results = [];
                var anchors = document.querySelectorAll('a[href*="/lightning/r/"]');
                for (var i = 0; i < anchors.length; i++) {
                    var href = anchors[i].getAttribute('href') || '';
                    var text = (anchors[i].innerText || anchors[i].textContent || '').trim();
                    if (href && text && text.length > 3) {
                        results.push({href: href, text: text});
                    }
                }
                return results;
            """)
        except Exception:
            links = []

        for link in links:
            url = link.get("href", "")
            name = link.get("text", "")
            if url and name and "/view" in url:
                # Make absolute
                if url.startswith("/"):
                    base = "/".join(driver.current_url.split("/")[:3])
                    url = base + url
                if url not in records:
                    records[url] = name

        # Check limit
        if limit > 0 and len(records) >= limit:
            break

        # Scroll
        try:
            new_height = driver.execute_script(
                f"window.scrollBy(0, {SCROLL_AMOUNT}); return document.body.scrollHeight;"
            )
        except Exception:
            break

        if new_height == prev_height:
            plateau_count += 1
            if plateau_count >= PLATEAU_STEPS:
                break
        else:
            plateau_count = 0
        prev_height = new_height

        time.sleep(SCROLL_PAUSE)

    result = [(name, url) for url, name in records.items()]
    print(f"  Collected {len(result)} record(s) from list")
    return result


# ─── Layer 1: Click Related tab (spec §9.2) ──────────────────────────────

CLICK_RELATED_JS = """
function clickRelated(root, depth) {
    if (depth > 14) return false;
    var els = root.querySelectorAll('a, button, [role="tab"]');
    for (var i = 0; i < els.length; i++) {
        var t = (els[i].innerText || els[i].textContent ||
                 els[i].getAttribute('title') || '').trim().toLowerCase();
        if (t === 'related') { els[i].click(); return true; }
    }
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        var sr = all[i].shadowRoot;
        if (sr && clickRelated(sr, depth + 1)) return true;
    }
    return false;
}
return clickRelated(document, 0);
"""


def click_related_tab(driver, ctrl=None, record_name: str = "") -> bool:
    """Find and click the Related tab using shadow DOM traversal.

    Retries until RELATED_TAB_TIMEOUT. Returns True on success.
    """
    end = time.time() + RELATED_TAB_TIMEOUT
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            result = driver.execute_script(CLICK_RELATED_JS)
            if result:
                settle(driver, 0.8, ctrl)
                return True
        except Exception:
            pass
        time.sleep(0.3)

    print(f"  ORDERS FAILURE: could not click Related tab for "
          f"'{record_name}' (timeout {RELATED_TAB_TIMEOUT}s)")
    return False


# ─── Layer 2: Find Orders related list link (spec §9.2) ─────────────────

FIND_ORDERS_LINK_JS = """
function findOrdersLink(root, depth) {
    if (depth > 16) return null;
    var els = root.querySelectorAll('a');
    for (var i = 0; i < els.length; i++) {
        var txt = (els[i].innerText || els[i].textContent || '').trim();
        var href = (els[i].getAttribute('href') || '');
        if (/orders/i.test(txt) && href.indexOf('/related/') !== -1)
            return { href: href, text: txt };
    }
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        var sr = all[i].shadowRoot;
        if (sr) { var v = findOrdersLink(sr, depth + 1); if (v) return v; }
    }
    return null;
}
return findOrdersLink(document, 0);
"""


def find_orders_link(driver, ctrl=None, record_name: str = "") -> str:
    """Find the Orders related-list link in the Related tab.

    Returns the full URL or empty string on failure.
    """
    end = time.time() + ORDERS_LINK_TIMEOUT
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            result = driver.execute_script(FIND_ORDERS_LINK_JS)
            if result and result.get("href"):
                href = result["href"]
                text = result.get("text", "")
                # Make absolute
                if href.startswith("/"):
                    base = "/".join(driver.current_url.split("/")[:3])
                    href = base + href
                print(f"  Orders link      : found — text='{text}' href='{href[-60:]}'")
                return href
        except Exception:
            pass
        time.sleep(0.5)

    print(f"  ORDERS FAILURE: Related tab clicked but no Orders related-list "
          f"link found for '{record_name}'")
    return ""


# ─── Layer 3: Scroll orders list and harvest links (spec §9.2) ──────────

def scroll_and_harvest_orders(driver, ctrl=None) -> list:
    """Scroll the Orders related-list page and collect all candidate links.

    Returns a list of {href, text} dicts.
    """
    wait_for_page(driver, ctrl)
    settle(driver, 1.5, ctrl)

    all_links = {}  # href -> text (dedup)
    prev_height = 0
    plateau_count = 0

    for step in range(MAX_SCROLL_STEPS):
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()

        # Harvest links
        try:
            links = driver.execute_script("""
                var results = [];
                var anchors = document.querySelectorAll('a[href*="/lightning/r/"]');
                for (var i = 0; i < anchors.length; i++) {
                    var href = anchors[i].getAttribute('href') || '';
                    var text = (anchors[i].innerText || anchors[i].textContent || '').trim();
                    if (href.indexOf('/view') !== -1) {
                        results.push({href: href, text: text});
                    }
                }
                return results;
            """)
        except Exception:
            links = []

        for link in links:
            href = link.get("href", "")
            if href and href not in all_links:
                all_links[href] = link.get("text", "")

        # Scroll
        try:
            new_height = driver.execute_script(
                f"window.scrollBy(0, {SCROLL_AMOUNT}); return document.body.scrollHeight;"
            )
        except Exception:
            break

        if new_height == prev_height:
            plateau_count += 1
            if plateau_count >= PLATEAU_STEPS:
                break
        else:
            plateau_count = 0
        prev_height = new_height

        time.sleep(SCROLL_PAUSE)

    return [{"href": h, "text": t} for h, t in all_links.items()]


# ─── Layer 4: Identify actual order records (spec §9.2) ─────────────────

def _extract_prefix(url: str) -> str:
    """Extract the 3-character Salesforce ID prefix from a /lightning/r/ID/view URL."""
    match = re.search(r'/lightning/r/([a-zA-Z0-9]{3,18})/view', url)
    if match:
        return match.group(1)[:3]
    return ""


def identify_order_urls(driver, candidates: list, ctrl=None) -> tuple:
    """Apply the 4-method detection stack to identify genuine order URLs.

    Returns (order_urls: list, prefix: str, method: str)
    """
    if not candidates:
        return [], "", "none"

    # Method A — Scoped container harvesting
    # Try to find order links within a container specific to orders
    try:
        scoped = driver.execute_script("""
            function findOrderContainer(root, depth) {
                if (depth > 12) return [];
                var containers = root.querySelectorAll(
                    '[data-component-id*="related"], force-related-list-container, ' +
                    '[class*="relatedList"]'
                );
                var results = [];
                for (var c = 0; c < containers.length; c++) {
                    var heading = (containers[c].innerText || '').substring(0, 200);
                    if (/orders/i.test(heading)) {
                        var links = containers[c].querySelectorAll('a[href*="/lightning/r/"]');
                        for (var i = 0; i < links.length; i++) {
                            var href = links[i].getAttribute('href') || '';
                            if (href.indexOf('/view') !== -1) results.push(href);
                        }
                    }
                }
                var all = root.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                    var sr = all[i].shadowRoot;
                    if (sr) {
                        var sub = findOrderContainer(sr, depth + 1);
                        results = results.concat(sub);
                    }
                }
                return results;
            }
            return findOrderContainer(document, 0);
        """)
        if scoped and len(scoped) >= 3:
            prefix = _extract_prefix(scoped[0])
            print(f"  ORDERS: {len(scoped)} order URLs found | prefix '{prefix}' | "
                  f"Method A (scoped container)")
            return scoped, prefix, "Method A"
    except Exception:
        pass

    # Method B — Dominant prefix detection with exclusion list
    prefix_count = {}
    for cand in candidates:
        href = cand.get("href", "")
        prefix = _extract_prefix(href)
        if prefix and prefix not in NON_ORDER_PREFIXES:
            prefix_count[prefix] = prefix_count.get(prefix, 0) + 1

    if prefix_count:
        sorted_prefixes = sorted(prefix_count.items(), key=lambda x: x[1], reverse=True)
        top_prefix, top_count = sorted_prefixes[0]

        # Confidence check: top prefix should dominate
        if top_count >= 3 or len(sorted_prefixes) == 1:
            order_urls = [
                c["href"] for c in candidates
                if _extract_prefix(c["href"]) == top_prefix
            ]
            print(f"  ORDERS: {len(order_urls)} order URLs found | prefix '{top_prefix}' | "
                  f"Method B (dominant prefix)")
            return order_urls, top_prefix, "Method B"

        # Uncertain — Method C: sample verification
        if len(sorted_prefixes) >= 2:
            print(f"  ORDERS: Prefix detection uncertain — "
                  f"top candidates: {', '.join(f'{p} x{c}' for p, c in sorted_prefixes[:3])}")

    # Method C — Sample verification fallback
    for prefix_candidate, count in sorted(prefix_count.items(),
                                           key=lambda x: x[1], reverse=True)[:3]:
        sample_urls = [
            c["href"] for c in candidates
            if _extract_prefix(c["href"]) == prefix_candidate
        ][:3]

        verified = 0
        for url in sample_urls:
            if ctrl:
                ctrl.poll()
            try:
                driver.get(url)
                wait_for_page(driver, ctrl, timeout=8)
                page_text = driver.execute_script(
                    "return document.body ? document.body.innerText : '';"
                )
                order_indicators = [
                    "Order Number", "Main POC", "Point of Contact",
                    "Status", "Attendee",
                ]
                if any(ind in page_text for ind in order_indicators):
                    verified += 1
            except Exception:
                pass

        if verified > 0:
            order_urls = [
                c["href"] for c in candidates
                if _extract_prefix(c["href"]) == prefix_candidate
            ]
            print(f"  ORDER PREFIX: sample verification passed for prefix "
                  f"'{prefix_candidate}' (checked {len(sample_urls)} samples, "
                  f"{verified} passed)")
            print(f"  ORDERS: {len(order_urls)} order URLs found | prefix "
                  f"'{prefix_candidate}' | Method C (sample verification)")
            return order_urls, prefix_candidate, "Method C"

    # Method D — Visible row text capture (last resort)
    # Accept all non-excluded links if they look order-like
    non_excluded = [
        c["href"] for c in candidates
        if _extract_prefix(c["href"]) not in NON_ORDER_PREFIXES
    ]
    if non_excluded:
        prefix = _extract_prefix(non_excluded[0])
        print(f"  ORDERS: {len(non_excluded)} candidate URLs (unverified) | "
              f"prefix '{prefix}' | Method D (row text fallback)")
        return non_excluded, prefix, "Method D"

    print("  ORDERS FAILURE: no order URLs identified from candidates")
    return [], "", "none"


# ─── Full order-finding pipeline ────────────────────────────────────────────

def find_order_urls(driver, record_url: str, record_name: str,
                    ctrl=None) -> tuple:
    """Complete 4-layer order URL discovery for a record.

    Returns (order_urls: list, prefix: str, method: str)
    """
    # Navigate to record
    driver.get(record_url)
    wait_for_page(driver, ctrl)
    settle(driver, 1.0, ctrl)

    # Layer 1 — Click Related tab
    if not click_related_tab(driver, ctrl, record_name):
        return [], "", "failed_L1"

    # Layer 2 — Find Orders link
    orders_url = find_orders_link(driver, ctrl, record_name)
    if not orders_url:
        return [], "", "failed_L2"

    # Navigate to the full Orders related-list page
    driver.get(orders_url)
    wait_for_page(driver, ctrl)
    settle(driver, 1.5, ctrl)

    # Layer 3 — Scroll and harvest
    candidates = scroll_and_harvest_orders(driver, ctrl)
    if not candidates:
        print(f"  ORDERS FAILURE: Orders page loaded but 0 candidate links "
              f"collected after scroll")
        return [], "", "failed_L3"

    # Layer 4 — Identify actual orders
    return identify_order_urls(driver, candidates, ctrl)


# ─── Per-record progress (spec §9.3) ──────────────────────────────────────

def load_progress(output_folder: str, record_name: str) -> dict:
    """Load per-record progress JSON."""
    path = os.path.join(output_folder, f"{safe_stem(record_name)}_progress.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"record_name": record_name, "done": [], "updated": ""}


def save_progress(output_folder: str, record_name: str, progress: dict):
    """Atomic write of per-record progress JSON."""
    stem = safe_stem(record_name)
    final = os.path.join(output_folder, f"{stem}_progress.json")
    tmp = final + ".tmp"
    progress["updated"] = datetime.now().isoformat()
    try:
        with open(tmp, "w") as f:
            json.dump(progress, f, indent=2)
        os.replace(tmp, final)
    except Exception as e:
        print(f"  Warning: could not save progress: {e}")


# ─── Contact page navigation ───────────────────────────────────────────────

FIND_POC_JS = """
function findPOC(root, depth) {
    if (depth > 14) return null;
    var links = root.querySelectorAll('a[href*="/lightning/r/003"]');
    for (var i = 0; i < links.length; i++) {
        var href = links[i].getAttribute('href') || '';
        if (href.indexOf('/view') !== -1) return href;
    }
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        var sr = all[i].shadowRoot;
        if (sr) { var v = findPOC(sr, depth + 1); if (v) return v; }
    }
    return null;
}
return findPOC(document, 0);
"""


def navigate_to_poc(driver, ctrl=None) -> str:
    """Find and navigate to the Point of Contact from an order page.

    Returns the contact URL or empty string on failure.
    """
    end = time.time() + POC_TIMEOUT
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            result = driver.execute_script(FIND_POC_JS)
            if result:
                if result.startswith("/"):
                    base = "/".join(driver.current_url.split("/")[:3])
                    result = base + result
                driver.get(result)
                wait_for_page(driver, ctrl)
                settle(driver, 0.5, ctrl)
                return result
        except Exception:
            pass
        time.sleep(0.3)

    return ""


def wait_for_contact_ready(driver, ctrl=None) -> bool:
    """Wait for the contact page to be fully rendered."""
    end = time.time() + CONTACT_READY_TIMEOUT
    while time.time() < end:
        if ctrl:
            ctrl.poll()
            ctrl.wait_if_paused()
        try:
            # Check for a known contact page indicator
            ready = driver.execute_script("""
                function check(root, depth) {
                    if (depth > 10) return false;
                    var els = root.querySelectorAll('[class*="fields"], [class*="detail"]');
                    if (els.length > 0) return true;
                    var all = root.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var sr = all[i].shadowRoot;
                        if (sr && check(sr, depth + 1)) return true;
                    }
                    return false;
                }
                return check(document, 0);
            """)
            if ready:
                return True
        except Exception:
            pass
        time.sleep(CONTACT_READY_POLL)

    return False
