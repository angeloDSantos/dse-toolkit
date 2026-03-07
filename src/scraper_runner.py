"""
src/scraper_runner.py — Background thread wrapper for the scraper.

Bridges the scraper to the Flask web app:
  • Runs in a background thread (not subprocess)
  • Logs every contact event to the DB (scrape_events table)
  • Updates session counters in real time
  • Can be stopped from the web UI via a threading.Event
"""

import os
import sys
import time
import threading
from datetime import datetime

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    SF_USERNAME, SF_PASSWORD, SF_EVENTS_URL, SF_CAMPAIGNS_URL,
    BETWEEN_ORDERS, SCRAPES_ROOT,
)

# Global runner state
_runner_lock = threading.Lock()
_active_session_id = None
_stop_event = threading.Event()
_runner_thread = None


def is_running():
    """Check if a scrape is currently running."""
    with _runner_lock:
        return _runner_thread is not None and _runner_thread.is_alive()


def get_active_session_id():
    with _runner_lock:
        return _active_session_id


def request_stop():
    """Signal the running scraper to stop."""
    _stop_event.set()


def start_scrape(config: dict):
    """Start the scraper in a background thread.

    config = {
        scrape_name, entity_type, mode, keywords: [],
        record_region, phone_region, warning_exclusions: set(),
        list_limit: int,
    }

    Returns the session_id.
    """
    global _active_session_id, _runner_thread

    if is_running():
        raise RuntimeError("A scrape is already running")

    from core.database import create_scrape_session

    session_id = create_scrape_session(
        scrape_name=config["scrape_name"],
        entity_type=config["entity_type"],
        mode=config["mode"],
        keywords=config.get("keywords", []),
        record_region=config.get("record_region", "all"),
        phone_region=config.get("phone_region", "all"),
        warning_excl=config.get("warning_exclusions", set()),
        list_limit=config.get("list_limit", 500),
    )

    _stop_event.clear()

    with _runner_lock:
        _active_session_id = session_id

    _runner_thread = threading.Thread(
        target=_run_scrape,
        args=(session_id, config),
        daemon=True,
        name=f"scraper-{session_id}",
    )
    _runner_thread.start()
    return session_id


def _log_event(session_id, event_type, **kwargs):
    """Thread-safe event logging."""
    try:
        from core.database import add_scrape_event
        add_scrape_event(session_id, event_type, **kwargs)
    except Exception as e:
        print(f"  [RUNNER] Event log error: {e}")


def _update_session(session_id, **kwargs):
    """Thread-safe session update."""
    try:
        from core.database import update_scrape_session
        update_scrape_session(session_id, **kwargs)
    except Exception as e:
        print(f"  [RUNNER] Session update error: {e}")


def _run_scrape(session_id: int, config: dict):
    """Main scrape function running in background thread."""
    global _active_session_id

    # Import scraper modules
    from src.driver import create_driver, settle, wait_for_page, auto_login, submit_mfa_code
    from src.driver import _is_mfa_page, _is_logged_in
    from src.signals import Controls, SkipEventSignal, QuitSignal
    from src.filters import (
        expand_keywords, record_passes_filter, is_zmtbe,
        detect_warnings, warning_is_excluded,
        is_sponsor, is_delegate,
    )
    from src.navigation import (
        collect_list_records, find_order_urls, navigate_to_poc,
        wait_for_contact_ready,
    )
    from src.parsing import parse_contact
    from src.phone import resolve_best_phone
    from core.database import add_contact, get_db

    # Counters
    contacts_saved = 0
    ddi_saved = 0
    orders_done = 0
    sponsors_found = 0
    non_delegates = 0
    skipped = 0

    driver = None

    try:
        # Log start
        _log_event(session_id, "status", reason="Scrape starting — launching browser...")

        # Create driver
        driver = create_driver(worker_id=0)

        # Auto-login
        _log_event(session_id, "status", reason="Logging in to Salesforce...")
        auto_login(driver, SF_USERNAME, SF_PASSWORD)

        # Check if MFA is actually needed (might already be logged in)
        import time as _time
        _time.sleep(2)  # Let page settle after login submit

        if _is_logged_in(driver):
            _log_event(session_id, "status", reason="Already logged in — no MFA needed")
        elif _is_mfa_page(driver):
            # MFA required — wait for code from web UI
            _log_event(session_id, "mfa_required", reason="Enter MFA code in the web UI")
            _update_session(session_id, status="mfa_required")

            mfa_code = _wait_for_mfa_code(session_id)
            if mfa_code is None:
                _log_event(session_id, "status", reason="Scrape cancelled during MFA")
                _update_session(session_id, status="stopped",
                               finished_at=datetime.now().isoformat())
                return

            submit_mfa_code(driver, mfa_code)

            # Verify MFA worked
            _time.sleep(3)
            if not _is_logged_in(driver):
                _log_event(session_id, "error",
                           reason="MFA code rejected — login failed")
                _update_session(session_id, status="error",
                               finished_at=datetime.now().isoformat())
                return

            _log_event(session_id, "status", reason="MFA verified — login complete")
        else:
            _log_event(session_id, "error",
                       reason=f"Login page in unknown state: {driver.current_url}")
            _update_session(session_id, status="error",
                           finished_at=datetime.now().isoformat())
            return

        _update_session(session_id, status="running")

        # Controls (for poll/pause in navigation helpers — uses signal file,
        # but we also check _stop_event directly)
        ctrl = Controls()

        # Process entities
        entity_type = config["entity_type"]
        entities = []
        if entity_type in ("events", "both"):
            entities.append(("events", SF_EVENTS_URL))
        if entity_type in ("campaigns", "both"):
            entities.append(("campaigns", SF_CAMPAIGNS_URL))

        for etype, list_url in entities:
            if _stop_event.is_set():
                break

            _log_event(session_id, "status",
                       reason=f"Loading {etype} list...")
            _update_session(session_id, current_record=f"Loading {etype}...")

            driver.get(list_url)
            wait_for_page(driver, ctrl)
            settle(driver, 2.0, ctrl)

            # Collect records
            records = collect_list_records(driver, ctrl, config.get("list_limit", 500))
            _log_event(session_id, "status",
                       reason=f"Found {len(records)} {etype} records")

            # Filter ZMTBE
            records = [(n, u) for n, u in records if not is_zmtbe(n)]

            # Keyword filter
            if config["mode"] != "all" and config.get("keywords"):
                expanded = expand_keywords(
                    config["keywords"],
                    config.get("record_region", "all")
                )
                records = [
                    (n, u) for n, u in records
                    if record_passes_filter(n, expanded, config["mode"])
                ]

            _log_event(session_id, "status",
                       reason=f"Processing {len(records)} {etype} after filtering")

            for rec_idx, (rec_name, rec_url) in enumerate(records, 1):
                if _stop_event.is_set():
                    break

                _update_session(session_id,
                                current_record=f"[{rec_idx}/{len(records)}] {rec_name}")
                _log_event(session_id, "record_start",
                           record_name=rec_name, reason=f"Record {rec_idx}/{len(records)}")

                # Find orders
                try:
                    order_urls, prefix, method = find_order_urls(
                        driver, rec_url, rec_name, ctrl
                    )
                except SkipEventSignal:
                    _log_event(session_id, "skipped",
                               record_name=rec_name, reason="Skipped by signal")
                    skipped += 1
                    continue

                if not order_urls:
                    _log_event(session_id, "skipped",
                               record_name=rec_name,
                               reason="No orders found")
                    skipped += 1
                    continue

                _log_event(session_id, "status",
                           record_name=rec_name,
                           reason=f"{len(order_urls)} orders | prefix {prefix} | {method}")

                # Process each order
                for ord_idx, order_url in enumerate(order_urls, 1):
                    if _stop_event.is_set():
                        break

                    try:
                        # Navigate to order → POC
                        driver.get(order_url)
                        wait_for_page(driver, ctrl)
                        settle(driver, 0.5, ctrl)

                        contact_url = navigate_to_poc(driver, ctrl)
                        if not contact_url:
                            _log_event(session_id, "skipped",
                                       record_name=rec_name, order_url=order_url,
                                       reason="Could not find POC")
                            skipped += 1
                            continue

                        wait_for_contact_ready(driver, ctrl)
                        contact = parse_contact(driver)

                        crt = contact.get("contact_record_type", "")
                        rt = contact.get("record_type", "")

                        # Sponsor check
                        if is_sponsor(crt, rt):
                            sponsors_found += 1
                            _log_event(session_id, "sponsor",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name,
                                       contact_url=contact_url,
                                       reason="Sponsor — not logged")
                            _update_session(session_id, sponsors_found=sponsors_found)
                            continue

                        # Delegate check
                        if not is_delegate(crt):
                            non_delegates += 1
                            _log_event(session_id, "non_delegate",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name,
                                       contact_url=contact_url,
                                       reason=f"Non-delegate: {crt or '(empty)'}")
                            _update_session(session_id, non_delegates=non_delegates)
                            continue

                        # Warning check
                        warnings_raw = contact.get("warnings_raw", "")
                        detected = detect_warnings(warnings_raw)
                        excl_set = config.get("warning_exclusions", set())
                        should_skip, skip_reason = warning_is_excluded(detected, excl_set)

                        if should_skip:
                            skipped += 1
                            _log_event(session_id, "warning_skip",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name,
                                       warnings=warnings_raw,
                                       reason=skip_reason)
                            _update_session(session_id, skipped=skipped)
                            continue

                        # Phone validation
                        phone_region = config.get("phone_region", "all")
                        mode = config["mode"]
                        best_phone, phone_label, ddi = resolve_best_phone(
                            contact, phone_region, mode
                        )

                        if mode in ("mobile", "full") and not best_phone:
                            skipped += 1
                            _log_event(session_id, "skipped",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name,
                                       reason=f"No valid phone for mode '{mode}'")
                            _update_session(session_id, skipped=skipped)
                            continue

                        if mode == "email" and not contact.get("email"):
                            skipped += 1
                            _log_event(session_id, "skipped",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name,
                                       reason="No email found")
                            _update_session(session_id, skipped=skipped)
                            continue

                        # SAVED — log to DB and add to contacts master
                        contacts_saved += 1
                        orders_done += 1

                        _log_event(session_id, "saved",
                                   first_name=contact.get("first_name", ""),
                                   last_name=contact.get("last_name", ""),
                                   company=contact.get("company", ""),
                                   title=contact.get("title", ""),
                                   email=contact.get("email", ""),
                                   phone=best_phone,
                                   record_name=rec_name,
                                   order_url=order_url,
                                   contact_url=contact_url,
                                   warnings=warnings_raw)

                        # Also add to main contacts DB
                        try:
                            add_contact(
                                first_name=contact.get("first_name", ""),
                                last_name=contact.get("last_name", ""),
                                company=contact.get("company", ""),
                                title=contact.get("title", ""),
                                email=contact.get("email", ""),
                                phone=best_phone,
                                salesforce_id=contact_url,
                                source=f"scrape:{config['scrape_name']}",
                            )
                        except Exception:
                            pass

                        if ddi:
                            ddi_saved += 1

                        _update_session(session_id,
                                        contacts_saved=contacts_saved,
                                        ddi_saved=ddi_saved,
                                        orders_done=orders_done)

                    except SkipEventSignal:
                        _log_event(session_id, "skipped",
                                   record_name=rec_name, reason="Skipped by signal")
                        skipped += 1
                        break
                    except Exception as e:
                        _log_event(session_id, "error",
                                   record_name=rec_name,
                                   reason=f"Error: {str(e)[:200]}")

                    time.sleep(BETWEEN_ORDERS)

        # Completed
        end_status = "stopped" if _stop_event.is_set() else "completed"
        _log_event(session_id, "status",
                   reason=f"Scrape {end_status} — {contacts_saved} contacts saved")
        _update_session(session_id,
                        status=end_status,
                        contacts_saved=contacts_saved,
                        ddi_saved=ddi_saved,
                        orders_done=orders_done,
                        sponsors_found=sponsors_found,
                        non_delegates=non_delegates,
                        skipped=skipped,
                        finished_at=datetime.now().isoformat())

    except Exception as e:
        _log_event(session_id, "error", reason=f"Fatal: {str(e)[:300]}")
        _update_session(session_id, status="error",
                        finished_at=datetime.now().isoformat())
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        with _runner_lock:
            _active_session_id = None


# ─── MFA code exchange ──────────────────────────────────────────────────────

_mfa_code = None
_mfa_event = threading.Event()


def submit_mfa_from_web(code: str):
    """Called from the Flask route when user enters MFA code."""
    global _mfa_code
    _mfa_code = code
    _mfa_event.set()


def _wait_for_mfa_code(session_id: int, timeout: int = 300) -> str:
    """Block until MFA code is provided from the web UI or timeout."""
    global _mfa_code
    _mfa_code = None
    _mfa_event.clear()

    # Poll with timeout, checking stop too
    start = time.time()
    while time.time() - start < timeout:
        if _stop_event.is_set():
            return None
        if _mfa_event.wait(timeout=1.0):
            code = _mfa_code
            _mfa_code = None
            _mfa_event.clear()
            return code

    return None
