"""
src/parallel.py — Multi-worker parallel scraping engine.

Splits a work queue across N Selenium instances, each in its own
Chrome profile.  Thread-safe CSV writes, shared progress, and
unified signal handling.
"""

import os
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.driver import create_driver, settle, base_url
from src.signals import Controls, SkipEventSignal, QuitSignal
from src.navigation import (
    click_related_tab, navigate_to_orders_page, collect_order_urls,
    go_to_poc, wait_for_contact_ready, get_page_text, get_record_name,
    safe_stem, load_order_progress, save_order_progress,
)
from src.parsing import parse_contact
from src.csv_io import init_csv, save_row, headers_for_mode, H_DDI, count_csv_rows
from src.filters import warning_is_excluded
from config import (
    BETWEEN_ORDERS, SAVE_EVERY, CONTACT_READY_TIMEOUT,
    CHROME_PROFILE_DIR, WORKER_STAGGER_DELAY,
)


# ─── Status constants ───────────────────────────────────────────────────────

STATUS_DONE     = "done"
STATUS_FAILED   = "failed"
STATUS_EMPTY    = "empty"
STATUS_SKIPPED  = "skipped"

# ─── Thread-safe stats ──────────────────────────────────────────────────────

class SessionStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.saved = 0
        self.sponsors = 0
        self.non_delegates = 0
        self.skipped = 0
        self.ddi = 0
        self.orders = 0
        self.errors = 0
        self.start_time = time.time()

    def add(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, getattr(self, k, 0) + v)

    def elapsed(self):
        return time.time() - self.start_time

    def summary_str(self):
        m, s = divmod(int(self.elapsed()), 60)
        h, m = divmod(m, 60)
        t = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
        return (
            f"saved={self.saved}  ddi={self.ddi}  orders={self.orders}  "
            f"sponsors={self.sponsors}  non-delegates={self.non_delegates}  "
            f"skipped={self.skipped}  errors={self.errors}  elapsed={t}"
        )


# ─── Scrape a single record ─────────────────────────────────────────────────

def _scrape_record(driver, ctrl, record_url, mode, phone_region,
                   folder, warning_exclusions, list_name="",
                   worker_id=0):
    """Scrape one event/campaign record and save to CSV."""

    prefix = f"[W{worker_id}]" if worker_id > 0 else "  "
    stats = dict(saved=0, sponsors=0, non_delegates=0, skipped=0, ddi=0, orders=0)

    driver.get(record_url)
    settle(driver, 0.6, ctrl)

    # Get record name
    name = list_name or get_record_name(driver) or "unnamed"
    print(f"{prefix} Record: {name}")
    stem = safe_stem(name)

    # Check exclusion
    kw = ctrl.name_matches_exclusion(name)
    if kw:
        print(f"{prefix}  [EXCLUDED] matches '{kw}'")
        raise SkipEventSignal()

    p_main = os.path.join(folder, f"{stem}.csv")
    p_ddi  = os.path.join(folder, f"{stem}_DDI.csv")
    init_csv(p_main, headers_for_mode(mode))
    init_csv(p_ddi, H_DDI)

    done_orders = load_order_progress(folder, stem)
    if done_orders:
        print(f"{prefix}  Resuming — {len(done_orders)} orders already done.")

    # Click Related tab
    if not click_related_tab(driver, ctrl, timeout=8):
        print(f"{prefix}  ERROR: Could not click Related tab.")
        return STATUS_FAILED, stats

    settle(driver, 1.0, ctrl)

    # Navigate to Orders
    if not navigate_to_orders_page(driver, ctrl, timeout=10, record_url=record_url):
        print(f"{prefix}  ERROR: No Orders link found.")
        return STATUS_FAILED, stats

    # Collect order URLs
    order_urls = collect_order_urls(driver, ctrl)
    if not order_urls:
        print(f"{prefix}  WARNING: 0 orders found for '{name}'")
        return STATUS_EMPTY, stats

    new_urls = [u for u in order_urls if u not in done_orders]
    print(f"{prefix}  {len(order_urls)} orders | {len(done_orders)} done | {len(new_urls)} new")

    # Process each order
    for idx, order_url in enumerate(new_urls, 1):
        ctrl.poll()
        ctrl.wait_if_paused()

        if ctrl.consume_skip_contact():
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue

        print(f"{prefix}  [{idx}/{len(new_urls)}] {order_url}")
        driver.get(order_url)
        settle(driver, 0.25, ctrl)

        if not go_to_poc(driver, ctrl, timeout=7):
            print(f"{prefix}    No POC — skipping.")
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue

        contact_url = driver.current_url
        if "/lightning/r/Contact/" not in contact_url:
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue

        ok, why = wait_for_contact_ready(driver, ctrl, timeout=CONTACT_READY_TIMEOUT)
        if not ok:
            print(f"{prefix}    Contact not ready: {why}")
            if "interruption" in why or "timeout" in why:
                ctrl.pause()
                ctrl.wait_if_paused()
            ok2, _ = wait_for_contact_ready(driver, ctrl, timeout=10)
            if not ok2:
                done_orders.add(order_url)
                stats["skipped"] += 1
                continue

        text = get_page_text(driver)
        data = parse_contact(text, phone_region)
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Sponsor/non-delegate filtering
        crt = (data.get("contact_record_type") or "").strip().lower()
        rt  = (data.get("record_type") or "").strip().lower()
        if "sponsor" in crt or "sponsor" in rt:
            stats["sponsors"] += 1
            print(
                f"{prefix}    SPONSOR — {data['first_name']} {data['last_name']} | "
                f"CRT='{data.get('contact_record_type','')}' RT='{data.get('record_type','')}' — NOT SAVED"
            )
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue
        if crt and crt != "delegate":
            stats["non_delegates"] += 1
            print(
                f"{prefix}    NON-DELEGATE — {data['first_name']} {data['last_name']} | "
                f"CRT='{data.get('contact_record_type','')}' — NOT SAVED"
            )
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue

        # Warning exclusion
        skip_warn, skip_reason = warning_is_excluded(
            data.get("warnings", ""), warning_exclusions)
        if skip_warn:
            print(
                f"{prefix}    EXCLUDED ({skip_reason}) — {data['first_name']} {data['last_name']} | "
                f"Warning: {data.get('warnings','')} — NOT SAVED"
            )
            done_orders.add(order_url)
            stats["skipped"] += 1
            continue

        # DDI
        if data["ddi_ok"] and data["ddi"]:
            save_row(p_ddi, [
                data["first_name"], data["last_name"], data["account"],
                data["ddi"], data["warnings"], contact_url, order_url,
            ])
            stats["ddi"] += 1

        # Save based on mode
        warn_display = f" | WARNING: {data['warnings']}" if data["warnings"] else ""

        if mode == "all":
            save_row(p_main, [
                data["first_name"], data["last_name"], data["account"],
                data["title"], data["email"], data["secondary_email"],
                data["phone"], data["warnings"], contact_url, order_url, now,
            ])
            stats["saved"] += 1
            print(f"{prefix}    SAVED: {data['first_name']} {data['last_name']}{warn_display}")

        elif mode == "mobile":
            if data["phone"]:
                save_row(p_main, [
                    data["first_name"], data["last_name"], data["account"],
                    data["phone"], data["warnings"], now, contact_url, order_url,
                ])
                stats["saved"] += 1
                print(f"{prefix}    SAVED: {data['first_name']} {data['last_name']} | {data['phone']}{warn_display}")
            else:
                print(f"{prefix}    NO MOBILE: {data['first_name']} {data['last_name']}")

        elif mode == "email":
            if data["email"]:
                save_row(p_main, [
                    data["first_name"], data["last_name"], data["account"],
                    data["email"], data["warnings"], now, contact_url, order_url,
                ])
                stats["saved"] += 1
                print(f"{prefix}    SAVED: {data['first_name']} {data['last_name']} | {data['email']}{warn_display}")
            else:
                print(f"{prefix}    NO EMAIL: {data['first_name']} {data['last_name']}")

        else:  # full
            if data["first_name"] or data["phone"] or data["email"]:
                save_row(p_main, [
                    data["first_name"], data["last_name"], data["account"],
                    data["title"], data["phone"], data["email"],
                    data["warnings"], now, contact_url, order_url,
                ])
                stats["saved"] += 1
                print(f"{prefix}    SAVED: {data['first_name']} {data['last_name']}{warn_display}")
            else:
                print(f"{prefix}    NO DATA{warn_display}")

        done_orders.add(order_url)
        stats["orders"] += 1

        if idx % SAVE_EVERY == 0:
            save_order_progress(folder, stem, done_orders)

        time.sleep(BETWEEN_ORDERS)

    save_order_progress(folder, stem, done_orders)
    return STATUS_DONE, stats


# ─── Worker function (runs in thread) ────────────────────────────────────────

def _worker_fn(worker_id, driver, ctrl, records, mode, phone_region,
               folder, warning_exclusions, session_stats):
    """
    Process a chunk of records in a single Selenium instance.
    """
    prefix = f"[W{worker_id}]"
    print(f"{prefix} Starting — {len(records)} records to process")

    for idx, (rec_url, rec_name, _reason) in enumerate(records, 1):
        if ctrl.quit_requested():
            print(f"{prefix} Quit signal — stopping.")
            break

        ctrl.poll()
        ctrl.wait_if_paused()

        kw = ctrl.name_matches_exclusion(rec_name)
        if kw:
            print(f"{prefix} [EXCLUDED] '{rec_name}' matches '{kw}'")
            continue

        print(f"\n{'='*48}")
        print(f"{prefix} RECORD {idx}/{len(records)} — {rec_name or rec_url}")
        print(f"{'='*48}")

        try:
            status, stats = _scrape_record(
                driver, ctrl, rec_url, mode, phone_region,
                folder, warning_exclusions, list_name=rec_name,
                worker_id=worker_id,
            )
            session_stats.add(**stats)

        except SkipEventSignal:
            print(f"{prefix} [SKIP] '{rec_name}'")
            continue

        except QuitSignal:
            print(f"{prefix} [QUIT]")
            break

        except Exception as e:
            session_stats.add(errors=1)
            print(f"{prefix} ERROR on '{rec_name}': {e}")
            continue

    print(f"{prefix} Finished — {session_stats.summary_str()}")


# ─── Public API: run scraping with N workers ─────────────────────────────────

def run_parallel(records: list, drivers: list, ctrl: Controls,
                 mode: str, phone_region: str, folder: str,
                 warning_exclusions: set, session_stats: SessionStats):
    """
    Split records across N drivers and run them in parallel threads.

    Parameters
    ----------
    records : list of (url, name, reason) tuples
    drivers : list of Selenium WebDriver instances (one per worker)
    """
    n_workers = len(drivers)
    if n_workers == 1:
        _worker_fn(1, drivers[0], ctrl, records, mode, phone_region,
                   folder, warning_exclusions, session_stats)
        return

    # Split records into roughly equal chunks
    chunks = [[] for _ in range(n_workers)]
    for i, rec in enumerate(records):
        chunks[i % n_workers].append(rec)

    print(f"\n  Splitting {len(records)} records across {n_workers} workers:")
    for i, chunk in enumerate(chunks):
        print(f"    Worker {i+1}: {len(chunk)} records")
    print()

    threads = []
    for i, (driver, chunk) in enumerate(zip(drivers, chunks)):
        if not chunk:
            continue
        t = threading.Thread(
            target=_worker_fn,
            args=(i + 1, driver, ctrl, chunk, mode, phone_region,
                  folder, warning_exclusions, session_stats),
            daemon=True,
        )
        threads.append(t)

    # Stagger launches
    for i, t in enumerate(threads):
        t.start()
        if i < len(threads) - 1:
            time.sleep(WORKER_STAGGER_DELAY)

    for t in threads:
        t.join()
