#!/usr/bin/env python3
"""
sf_bulk_scraper.py — Salesforce CRM Scraper (Rebuilt)
======================================================

Main entry point. Implements the full scraper lifecycle per the rebuild spec:
  • Auto-login with saved credentials + sequential MFA
  • 10-step interactive setup with multi-keyword and multi-warning selection
  • Session model with scrape name + timestamped folders
  • 4-layer order finding with shadow DOM traversal
  • Structured contact parsing with phone normalisation
  • Signal-based control (skip, pause, quit, redo)
  • Post-stop and post-complete menus with resume/rerun/restart
  • Session summary at end of every run

Usage:
    Terminal 1:  python sf_bulk_scraper.py
    Terminal 2:  python signal.py [s|c|p|r|q|status]
"""

import os
import sys
import json
import time
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    SF_USERNAME, SF_PASSWORD, SF_EVENTS_URL, SF_CAMPAIGNS_URL,
    BETWEEN_ORDERS, SAVE_EVERY, SCRAPES_ROOT,
)
from src.driver import create_driver, settle, wait_for_page, login_all_workers
from src.signals import Controls, SkipEventSignal, QuitSignal
from src.setup import run_full_setup, print_confirmation_summary
from src.setup import _yn
from src.filters import (
    expand_keywords, record_passes_filter, is_zmtbe,
    detect_warnings, warning_is_excluded, describe_exclusions,
    is_sponsor, is_delegate,
)
from src.navigation import (
    collect_list_records, find_order_urls, navigate_to_poc,
    wait_for_contact_ready, load_progress, save_progress,
)
from src.parsing import parse_contact
from src.phone import resolve_best_phone
from src.csv_io import CSVWriter


# ─── Session state (spec §2.3) ─────────────────────────────────────────────

def write_session_state(config: dict):
    """Write session_state.json to the output folder."""
    path = os.path.join(config["output_folder"], "session_state.json")
    state = {
        "scrape_name": config["scrape_name"],
        "run_timestamp": config["run_timestamp"],
        "entity_type": config["entity_type"],
        "mode": config["mode"],
        "keywords": config.get("keywords", []),
        "record_region": config.get("record_region", "all"),
        "phone_region": config.get("phone_region", "all"),
        "warning_exclusions": list(config.get("warning_exclusions", set())),
        "list_limit": config.get("list_limit", 500),
        "run_mode": config.get("run_mode", "fresh"),
        "stopped_cleanly": config.get("stopped_cleanly", False),
        "completed_record_urls": config.get("completed_record_urls", []),
        "output_folder": config["output_folder"],
    }
    os.makedirs(config["output_folder"], exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def load_session_state(folder: str) -> dict:
    """Load session_state.json from a run folder."""
    path = os.path.join(folder, "session_state.json")
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        # Convert exclusions back to set
        data["warning_exclusions"] = set(data.get("warning_exclusions", []))
        return data
    return {}


def find_prior_runs(scrape_name: str) -> list:
    """Find prior run folders for a given scrape name."""
    scrape_dir = os.path.join(SCRAPES_ROOT, scrape_name)
    if not os.path.isdir(scrape_dir):
        return []
    runs = []
    for entry in sorted(os.listdir(scrape_dir)):
        run_dir = os.path.join(scrape_dir, entry)
        if os.path.isdir(run_dir):
            state_file = os.path.join(run_dir, "session_state.json")
            if os.path.exists(state_file):
                runs.append(run_dir)
    return runs


# ─── Session counters ──────────────────────────────────────────────────────

class SessionStats:
    """Running counters for the session summary."""
    def __init__(self):
        self.contacts_saved = 0
        self.ddi_saved = 0
        self.orders_processed = 0
        self.sponsors_found = 0
        self.non_delegates = 0
        self.skipped_other = 0
        self.warnings_skipped = 0


# ─── Session summary (spec §4.4 / §12.4) ──────────────────────────────────

def print_session_summary(config: dict, stats: SessionStats, end_reason: str):
    """Print the formatted session summary."""
    print()
    print("=" * 65)
    print("  SESSION SUMMARY")
    print("=" * 65)
    print(f"  Scrape name       : {config['scrape_name']}")
    print(f"  Run timestamp     : {config['run_timestamp']}")
    print(f"  Output folder     : {config['output_folder']}")
    excl = config.get("warning_exclusions", set())
    print(f"  Warning exclusions: {describe_exclusions(excl)}")
    print(f"  Contacts saved    : {stats.contacts_saved}")
    print(f"  DDI saved         : {stats.ddi_saved}")
    print(f"  Orders processed  : {stats.orders_processed}")
    print(f"  Sponsors found    : {stats.sponsors_found:>5d}    (none written to CSV)")
    print(f"  Non-delegates     : {stats.non_delegates:>5d}    (none written to CSV)")
    print(f"  Warnings skipped  : {stats.warnings_skipped}")
    print(f"  Skipped (other)   : {stats.skipped_other}")
    print(f"  Run ended         : {end_reason}")
    print("=" * 65)
    print()


# ─── Setup log (spec §14.1) ───────────────────────────────────────────────

def print_setup_log(config: dict):
    """Print the session start log."""
    print()
    print("=== SESSION START ===")
    print(f"Scrape name      : {config['scrape_name']}")
    print(f"Run timestamp    : {config['run_timestamp']}")
    print(f"Run mode         : {config.get('run_mode', 'fresh')}")
    print(f"Entity type      : {config['entity_type']}")
    kws = config.get("keywords", [])
    print(f"Keywords         : {' | '.join(kws) if kws else '(ALL SCRAPE)'}")
    print(f"Record region    : {config.get('record_region', 'all')}")
    print(f"Collection mode  : {config['mode']}")
    if config["mode"] in ("mobile", "full"):
        print(f"Phone region     : {config.get('phone_region', 'all')}")
    excl = config.get("warning_exclusions", set())
    print(f"Warning excl.    : {describe_exclusions(excl)}")
    print(f"List limit       : {config.get('list_limit', 500)}")
    print(f"Output folder    : {config['output_folder']}")
    print()


# ─── Process a single order/contact ────────────────────────────────────────

def process_order(driver, order_url: str, config: dict, csv_writer: CSVWriter,
                  stats: SessionStats, ctrl: Controls, order_index: int,
                  total_orders: int):
    """Navigate to an order, find POC, parse contact, validate, and write."""

    print(f"\n  [{order_index}/{total_orders}] {order_url[-60:]}")

    # Navigate to order page
    driver.get(order_url)
    wait_for_page(driver, ctrl)
    settle(driver, 0.5, ctrl)

    # Check for skip-contact signal
    if ctrl.consume_skip_contact():
        print("    Skipped by signal")
        stats.skipped_other += 1
        return

    # Navigate to POC
    contact_url = navigate_to_poc(driver, ctrl)
    if not contact_url:
        print("    Could not find POC — skipping")
        stats.skipped_other += 1
        return

    print(f"    Contact URL          : {contact_url[-60:]}")

    # Wait for contact page to render
    wait_for_contact_ready(driver, ctrl)

    # Parse contact fields
    contact = parse_contact(driver)

    # Sponsor check (spec §7.1)
    crt = contact.get("contact_record_type", "")
    rt = contact.get("record_type", "")

    if is_sponsor(crt, rt):
        stats.sponsors_found += 1
        print(f"    SPONSOR #{stats.sponsors_found} — NOT LOGGED TO CSV")
        print(f"      Contact              : {contact['first_name']} "
              f"{contact['last_name']} | {contact.get('email', '')}")
        print(f"      Contact Record Type  : {crt}")
        print(f"      Record Type          : {rt}")
        return

    # Delegate check (spec §7.2)
    if not is_delegate(crt):
        stats.non_delegates += 1
        print(f"    NON-DELEGATE #{stats.non_delegates} — NOT LOGGED TO CSV")
        print(f"      Contact              : {contact['first_name']} "
              f"{contact['last_name']}")
        print(f"      Contact Record Type  : {crt or '(empty)'}")
        return

    print(f"    Contact Record Type  : {crt}")
    print(f"    Record Type          : {rt}")
    print(f"    Sponsor check        : PASS")
    print(f"    Delegate check       : PASS")

    # Warning check (spec §6)
    warnings_raw = contact.get("warnings_raw", "")
    detected = detect_warnings(warnings_raw)
    excl_set = config.get("warning_exclusions", set())
    should_skip, skip_reason = warning_is_excluded(detected, excl_set)

    if detected:
        print(f"    Warnings detected    : {', '.join(sorted(detected))}")
    else:
        print(f"    Warnings detected    : (none)")

    if should_skip:
        stats.warnings_skipped += 1
        print(f"    Warning exclusion    : SKIP — {skip_reason}")
        print(f"    EXCLUDED ({skip_reason}) — NOT LOGGED TO CSV")
        print(f"      Contact   : {contact['first_name']} {contact['last_name']} "
              f"| {contact.get('email', '')}")
        print(f"      Warning   : \"{warnings_raw}\"")
        print(f"      Rule      : {skip_reason} in active exclusion set")
        return

    # Phone validation (spec §11)
    phone_region = config.get("phone_region", "all")
    mode = config["mode"]

    best_phone, phone_label, ddi = resolve_best_phone(
        contact, phone_region, mode
    )

    # For mobile/full mode, skip if no valid phone
    if mode in ("mobile", "full") and not best_phone:
        stats.skipped_other += 1
        print(f"    Result               : SKIPPED — no valid phone for mode '{mode}'")
        return

    # For email mode, skip if no email
    if mode == "email" and not contact.get("email"):
        stats.skipped_other += 1
        print(f"    Result               : SKIPPED — no email found")
        return

    # Write to CSV
    csv_writer.write_contact(
        contact, best_phone, contact_url, order_url, warnings_raw
    )
    stats.contacts_saved += 1
    stats.orders_processed += 1

    # Write DDI if present
    if ddi:
        csv_writer.write_ddi(contact, ddi, contact_url, order_url, warnings_raw)
        stats.ddi_saved += 1

    print(f"    ✓ SAVED — {contact['first_name']} {contact['last_name']} "
          f"| {contact.get('company', '')} | {best_phone or contact.get('email', '')}")


# ─── Process a single record (event or campaign) ──────────────────────────

def process_record(driver, record_name: str, record_url: str, record_index: int,
                   total_records: int, entity_type: str, config: dict,
                   stats: SessionStats, ctrl: Controls):
    """Process one event/campaign record: find orders, scrape contacts."""

    print()
    print(f"  RECORD {record_index}/{total_records} [{entity_type}] — {record_name}")
    print(f"    URL              : {record_url[-70:]}")

    # Find order URLs (4-layer method)
    order_urls, prefix, method = find_order_urls(
        driver, record_url, record_name, ctrl
    )

    if not order_urls:
        print(f"    No orders found — skipping record")
        stats.skipped_other += 1
        return

    # Load progress for resume support
    progress = load_progress(config["output_folder"], record_name)
    done_set = set(progress.get("done", []))
    already_done = len(done_set)
    to_scrape = [u for u in order_urls if u not in done_set]

    progress["record_name"] = record_name
    progress["record_url"] = record_url
    progress["order_prefix_used"] = prefix
    progress["detection_method"] = method

    print(f"    Orders collected : {len(order_urls)} URLs | prefix '{prefix}' | {method}")
    if already_done > 0:
        print(f"    Already done     : {already_done}")
    print(f"    To scrape        : {len(to_scrape)}")

    # Open CSV writer for this record
    csv_writer = CSVWriter(config["output_folder"], record_name, config["mode"])

    try:
        for i, order_url in enumerate(to_scrape, 1):
            # Check signals
            ctrl.poll()
            ctrl.wait_if_paused()

            if ctrl.consume_status():
                print(f"\n  [STATUS] Scrape: {config['scrape_name']} | "
                      f"Record: {record_name} | "
                      f"Order {i}/{len(to_scrape)} | "
                      f"Saved: {stats.contacts_saved}")

            process_order(
                driver, order_url, config, csv_writer,
                stats, ctrl, i, len(to_scrape)
            )

            # Mark as done
            progress.setdefault("done", []).append(order_url)

            # Periodic save
            if i % SAVE_EVERY == 0:
                save_progress(config["output_folder"], record_name, progress)

            time.sleep(BETWEEN_ORDERS)

    except SkipEventSignal:
        print(f"\n  [SIGNAL] Skipping record '{record_name}'")
    finally:
        # Always save progress and close CSV
        save_progress(config["output_folder"], record_name, progress)
        csv_writer.close()


# ─── Run entity type (events or campaigns) ────────────────────────────────

def run_entity(driver, entity_type: str, config: dict,
               stats: SessionStats, ctrl: Controls):
    """Scrape all records of a given entity type."""

    url = SF_EVENTS_URL if entity_type == "events" else SF_CAMPAIGNS_URL
    print(f"\n  Loading {entity_type} list...")

    driver.get(url)
    wait_for_page(driver, ctrl)
    settle(driver, 2.0, ctrl)

    # Collect record links
    records = collect_list_records(driver, ctrl, config.get("list_limit", 500))

    # Filter ZMTBE
    zmtbe_count = sum(1 for name, _ in records if is_zmtbe(name))
    if zmtbe_count > 0:
        records = [(n, u) for n, u in records if not is_zmtbe(n)]
        print(f"  Removed {zmtbe_count} ZMTBE event record(s).")

    # Filter by keywords (unless ALL SCRAPE)
    if config["mode"] != "all" and config.get("keywords"):
        expanded = expand_keywords(config["keywords"], config.get("record_region", "all"))
        filtered = [(n, u) for n, u in records
                     if record_passes_filter(n, expanded, config["mode"])]
        print(f"  Keyword filter: {len(filtered)}/{len(records)} records match")
        records = filtered

    # Skip already-completed records (for resume)
    completed = set(config.get("completed_record_urls", []))
    if completed:
        before = len(records)
        records = [(n, u) for n, u in records if u not in completed]
        print(f"  Resume: skipped {before - len(records)} already-completed records")

    print(f"\n  Processing {len(records)} {entity_type} record(s)...\n")

    record_index = 0
    while record_index < len(records):
        name, url = records[record_index]
        record_index += 1

        # Check for redo signal
        if ctrl.consume_redo() and record_index > 1:
            record_index -= 2
            print(f"  [SIGNAL] Redo — going back to previous record")
            continue

        try:
            process_record(
                driver, name, url, record_index, len(records),
                entity_type, config, stats, ctrl
            )
            # Mark record as completed
            config.setdefault("completed_record_urls", []).append(url)

        except SkipEventSignal:
            print(f"  Skipped record: {name}")
            continue


# ─── Post-stop menu (spec §4.2) ──────────────────────────────────────────

def post_stop_menu(config: dict) -> str:
    """Present the post-stop menu. Returns the action chosen."""
    print()
    print("  Scrape stopped. What would you like to do next?")
    print("    1) Resume this scrape — continue from where we stopped")
    print("    2) Rerun this scrape from scratch — same config, new run folder")
    print("    3) Start a different scrape — full setup from the beginning")
    print("    4) Exit")

    while True:
        ans = input("\n  → ").strip()
        if ans == "1":
            return "resume"
        elif ans == "2":
            return "rerun"
        elif ans == "3":
            return "new"
        elif ans == "4":
            return "exit"
        print("  Enter 1–4")


def post_complete_menu() -> str:
    """Present the post-complete menu (spec §4.1)."""
    print()
    print("  Scrape complete. What would you like to do next?")
    print("    1) Run another campaign / summit")
    print("    2) Exit")

    while True:
        ans = input("\n  → ").strip()
        if ans == "1":
            return "new"
        elif ans == "2":
            return "exit"
        print("  Enter 1 or 2")


# ─── Startup flow (spec §3.2) ────────────────────────────────────────────

def startup_flow() -> dict:
    """Handle first-time or returning startup."""

    # Check for prior runs
    if os.path.isdir(SCRAPES_ROOT):
        scrape_dirs = [d for d in os.listdir(SCRAPES_ROOT)
                       if os.path.isdir(os.path.join(SCRAPES_ROOT, d))]
        if scrape_dirs:
            print(f"\n  Prior scrapes found: {', '.join(scrape_dirs[-5:])}")
            latest = scrape_dirs[-1]
            runs = find_prior_runs(latest)
            if runs:
                last_state = load_session_state(runs[-1])
                if last_state:
                    print(f"\n  Prior scrape data found for '{latest}'.")
                    if _yn("  Are you scraping the same data as before?"):
                        print()
                        print("  Same data — how do you want to proceed?")
                        print("    1) Resume — continue from where the last run stopped")
                        print("    2) Fresh rerun — start from scratch (new timestamped "
                              "folder, prior files untouched)")
                        while True:
                            ans = input("\n  → ").strip()
                            if ans == "1":
                                # Resume
                                new_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                                last_state["run_timestamp"] = new_ts
                                new_folder = os.path.join(
                                    SCRAPES_ROOT, latest, new_ts
                                )
                                last_state["output_folder"] = new_folder
                                last_state["run_mode"] = "resume"
                                last_state["stopped_cleanly"] = False
                                os.makedirs(new_folder, exist_ok=True)
                                return last_state
                            elif ans == "2":
                                # Fresh rerun
                                new_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                                last_state["run_timestamp"] = new_ts
                                new_folder = os.path.join(
                                    SCRAPES_ROOT, latest, new_ts
                                )
                                last_state["output_folder"] = new_folder
                                last_state["run_mode"] = "fresh"
                                last_state["stopped_cleanly"] = False
                                last_state["completed_record_urls"] = []
                                os.makedirs(new_folder, exist_ok=True)
                                return last_state
                            print("  Enter 1 or 2")

    # No prior state or user wants fresh setup
    return run_full_setup()


# ─── Main loop ──────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 55)
    print("  SALESFORCE CRM SCRAPER — DSE TOOLKIT")
    print("=" * 55)
    print()

    # Create driver
    num_workers = 1
    print(f"  Starting {num_workers} browser window(s)...")
    driver = create_driver(worker_id=0)
    drivers = [driver]

    # Auto-login
    login_all_workers(drivers, SF_USERNAME, SF_PASSWORD)

    # Controls
    ctrl = Controls()

    # Main scraping loop
    action = "setup"  # Start with setup

    while True:
        try:
            # Get or resume config
            if action == "setup":
                config = startup_flow()
            elif action == "new":
                config = run_full_setup()
            # "resume" and "rerun" keep the config from post_stop_menu

            # Write session state
            write_session_state(config)
            print_setup_log(config)

            stats = SessionStats()

            # Run the scrape
            entity_type = config["entity_type"]
            if entity_type in ("events", "both"):
                run_entity(driver, "events", config, stats, ctrl)
            if entity_type in ("campaigns", "both"):
                run_entity(driver, "campaigns", config, stats, ctrl)

            # Natural completion
            config["stopped_cleanly"] = True
            write_session_state(config)
            print_session_summary(config, stats, "completed")

            action = post_complete_menu()
            if action == "exit":
                break

        except QuitSignal:
            # Clean stop
            print()
            print("  [SIGNAL] Quit received — finishing current contact then stopping.")
            print(f"    Flushing progress JSON...   done")
            print(f"    CSV rows flushed...         done")

            config["stopped_cleanly"] = True
            write_session_state(config)
            print(f"    Session state updated...    done")
            print(f"    Stopped cleanly at {datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
            print(f"    Orders completed this run:  {stats.orders_processed}")

            print_session_summary(config, stats, "stopped cleanly")

            action = post_stop_menu(config)
            if action == "exit":
                break
            elif action == "resume":
                # Keep config as-is
                continue
            elif action == "rerun":
                # Same config, new timestamp folder
                new_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                config["run_timestamp"] = new_ts
                config["output_folder"] = os.path.join(
                    SCRAPES_ROOT, config["scrape_name"], new_ts
                )
                config["run_mode"] = "fresh"
                config["completed_record_urls"] = []
                config["stopped_cleanly"] = False
                os.makedirs(config["output_folder"], exist_ok=True)
                continue
            elif action == "new":
                continue

        except KeyboardInterrupt:
            print("\n\n  Keyboard interrupt received. Exiting.")
            break
        except Exception as e:
            print(f"\n  ERROR: {e}")
            import traceback
            traceback.print_exc()
            action = post_stop_menu(config)
            if action == "exit":
                break

    # Cleanup
    print("\n  Closing browser...")
    try:
        driver.quit()
    except Exception:
        pass
    print("  Done! Goodbye.\n")


if __name__ == "__main__":
    main()
