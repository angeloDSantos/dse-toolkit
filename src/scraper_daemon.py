"""
src/scraper_daemon.py — Background daemon managing the scrape_sessions queue.
"""

import os
import sys
import time
import json
from datetime import datetime

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    SF_USERNAME, SF_PASSWORD, SF_EVENTS_URL, SF_CAMPAIGNS_URL,
    BETWEEN_ORDERS
)

import runtime
from core.database import (
    get_db, update_scrape_session, add_scrape_event, add_contact
)

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

def daemon_loop():
    runtime.write_tool_state("scraper", status="running")
    print(f"  [{datetime.now().time().strftime('%H:%M:%S')}] [DAEMON] Scraper queue manager started.")
    
    while True:
        # Check if UI requested us to stop the daemon itself
        state = runtime.read_tool_state("scraper")
        if state.get("status") == "stopped_requested":
            break
            
        db = get_db()
        # Look for pending jobs (queued or running that were interrupted)
        job = db.execute("SELECT * FROM scrape_sessions WHERE status IN ('queued') ORDER BY id ASC LIMIT 1").fetchone()
        
        if job:
            print(f"  [{datetime.now().time().strftime('%H:%M:%S')}] [DAEMON] Acquired job {job['id']}. Starting execution...")
            db.execute("UPDATE scrape_sessions SET status = 'running' WHERE id = ?", (job['id'],))
            db.commit()
            
            # Delete any stale mfa_code from runtime before starting
            runtime.write_tool_state("scraper", mfa_code=None)
            
            # Execute the mission
            try:
                execute_mission(job)
            except Exception as e:
                db.execute("UPDATE scrape_sessions SET status = 'error', finished_at = ? WHERE id = ?", (datetime.now().isoformat(), job['id']))
                db.commit()
                print(f"  [DAEMON] Mission failed: {e}")
                import traceback
                traceback.print_exc()
        else:
            # Idle sleep when no jobs
            time.sleep(5)
            
    runtime.write_tool_state("scraper", status="stopped")


def _log_event(session_id, event_type, **kwargs):
    """Event logging helper."""
    try:
        add_scrape_event(session_id, event_type, **kwargs)
    except Exception as e:
        print(f"  [RUNNER] Event log error: {e}")

def _wait_for_mfa_code(session_id: int, timeout: int = 300) -> str:
    """Block until MFA code is provided from the UI -> runtime state, or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        state = runtime.read_tool_state("scraper")
        
        # Check if stop requested
        if state.get("status") == "stopped_requested":
            return None
            
        # Check DB to see if UI cancelled the job
        db = get_db()
        row = db.execute("SELECT status FROM scrape_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row or row['status'] not in ('mfa_required', 'running'):
            return None
            
        code = state.get("mfa_code")
        if code:
            # Clear it out so we don't reuse it by accident
            runtime.write_tool_state("scraper", mfa_code=None)
            return code
            
        time.sleep(1)

    return None

def execute_mission(job):
    session_id = job["id"]
    
    # Check if we have parsed JSON config
    import json
    keywords = json.loads(job["keywords"]) if job["keywords"] else []
    warning_excl = set(json.loads(job["warning_excl"])) if job["warning_excl"] else set()
    
    config = {
        "scrape_name": job["scrape_name"],
        "entity_type": job["entity_type"],
        "mode": job["mode"],
        "keywords": keywords,
        "record_region": job["record_region"],
        "phone_region": job["phone_region"],
        "warning_exclusions": warning_excl,
        "list_limit": job["list_limit"],
    }
    
    # Cursors
    start_entity = job["current_entity_index"]
    start_order = job["current_order_index"]
    
    # Running Counts
    contacts_saved = job["contacts_saved"]
    ddi_saved = job["ddi_saved"]
    orders_done = job["orders_done"]
    sponsors_found = job["sponsors_found"]
    non_delegates = job["non_delegates"]
    skipped = job["skipped"]

    driver = None
    db = get_db()

    try:
        _log_event(session_id, "status", reason="Scrape starting — launching browser...")

        driver = create_driver(worker_id=0)

        _log_event(session_id, "status", reason="Logging in to Salesforce...")
        auto_login(driver, SF_USERNAME, SF_PASSWORD)

        time.sleep(2)  # Let page settle after login submit

        if _is_logged_in(driver):
            _log_event(session_id, "status", reason="Already logged in — no MFA needed")
        elif _is_mfa_page(driver):
            _log_event(session_id, "mfa_required", reason="Enter MFA code in the web UI")
            update_scrape_session(session_id, status="mfa_required")

            mfa_code = _wait_for_mfa_code(session_id)
            if mfa_code is None:
                _log_event(session_id, "status", reason="Scrape cancelled during MFA")
                update_scrape_session(session_id, status="stopped", finished_at=datetime.now().isoformat())
                return

            submit_mfa_code(driver, mfa_code)

            time.sleep(3)
            if not _is_logged_in(driver):
                _log_event(session_id, "error", reason="MFA code rejected — login failed")
                update_scrape_session(session_id, status="error", finished_at=datetime.now().isoformat())
                return

            _log_event(session_id, "status", reason="MFA verified — login complete")
        else:
            _log_event(session_id, "error", reason=f"Login page in unknown state: {driver.current_url}")
            update_scrape_session(session_id, status="error", finished_at=datetime.now().isoformat())
            return

        update_scrape_session(session_id, status="running")

        ctrl = Controls()

        entities = []
        if config["entity_type"] in ("events", "both"):
            entities.append(("events", SF_EVENTS_URL))
        if config["entity_type"] in ("campaigns", "both"):
            entities.append(("campaigns", SF_CAMPAIGNS_URL))

        for etype, list_url in entities:
            # Check for stop request
            st = runtime.read_tool_state("scraper")
            if st.get("status") == "stopped_requested":
                update_scrape_session(session_id, status="stopped", finished_at=datetime.now().isoformat())
                return
            
            job_st = db.execute("SELECT status FROM scrape_sessions WHERE id = ?", (session_id,)).fetchone()
            if job_st and job_st["status"] in ("paused", "stopped", "cancelled"):
                _log_event(session_id, "status", reason=f"Mission interrupted: {job_st['status']}")
                return

            _log_event(session_id, "status", reason=f"Loading {etype} list...")
            update_scrape_session(session_id, current_record=f"Loading {etype}...")

            driver.get(list_url)
            wait_for_page(driver, ctrl)
            settle(driver, 2.0, ctrl)

            records = collect_list_records(driver, ctrl, config.get("list_limit", 500))
            _log_event(session_id, "status", reason=f"Found {len(records)} {etype} records")

            records = [(n, u) for n, u in records if not is_zmtbe(n)]

            if config["mode"] != "all" and config.get("keywords"):
                expanded = expand_keywords(config["keywords"], config.get("record_region", "all"))
                records = [
                    (n, u) for n, u in records
                    if record_passes_filter(n, expanded, config["mode"])
                ]

            _log_event(session_id, "status", reason=f"Processing {len(records)} {etype} after filtering")

            for rec_idx, (rec_name, rec_url) in enumerate(records):
                # Check interruption
                job_st = db.execute("SELECT status FROM scrape_sessions WHERE id = ?", (session_id,)).fetchone()
                if job_st and job_st["status"] != "running":
                    _log_event(session_id, "status", reason=f"Mission interrupted: {job_st['status']}")
                    return
                st = runtime.read_tool_state("scraper")
                if st.get("status") == "stopped_requested":
                    update_scrape_session(session_id, status="stopped", finished_at=datetime.now().isoformat())
                    return

                # FAST-FORWARD RECOVERY: Skip completed entities
                if rec_idx < start_entity:
                    continue

                update_scrape_session(session_id, current_record=f"[{rec_idx+1}/{len(records)}] {rec_name}")
                _log_event(session_id, "record_start", record_name=rec_name, reason=f"Record {rec_idx+1}/{len(records)}")

                try:
                    order_urls, prefix, method = find_order_urls(driver, rec_url, rec_name, ctrl)
                except SkipEventSignal:
                    _log_event(session_id, "skipped", record_name=rec_name, reason="Skipped by signal")
                    skipped += 1
                    update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx+1, current_order_index=0)
                    start_order = 0
                    continue

                if not order_urls:
                    _log_event(session_id, "skipped", record_name=rec_name, reason="No orders found")
                    skipped += 1
                    update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx+1, current_order_index=0)
                    start_order = 0
                    continue

                _log_event(session_id, "status", record_name=rec_name, reason=f"{len(order_urls)} orders | prefix {prefix} | {method}")

                for ord_idx, order_url in enumerate(order_urls):
                    # FAST-FORWARD RECOVERY: Skip completed orders
                    if rec_idx == start_entity and ord_idx < start_order:
                        continue

                    # Check interruption
                    job_st = db.execute("SELECT status FROM scrape_sessions WHERE id = ?", (session_id,)).fetchone()
                    if job_st and job_st["status"] != "running":
                        return
                    st = runtime.read_tool_state("scraper")
                    if st.get("status") == "stopped_requested":
                        update_scrape_session(session_id, status="stopped", finished_at=datetime.now().isoformat())
                        return

                    try:
                        driver.get(order_url)
                        wait_for_page(driver, ctrl)
                        settle(driver, 0.5, ctrl)

                        contact_url = navigate_to_poc(driver, ctrl)
                        if not contact_url:
                            _log_event(session_id, "skipped", record_name=rec_name, order_url=order_url, reason="Could not find POC")
                            skipped += 1
                            update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

                        wait_for_contact_ready(driver, ctrl)
                        contact = parse_contact(driver)

                        crt = contact.get("contact_record_type", "")
                        rt = contact.get("record_type", "")

                        if is_sponsor(crt, rt):
                            sponsors_found += 1
                            _log_event(session_id, "sponsor",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name, contact_url=contact_url,
                                       reason="Sponsor — not logged")
                            update_scrape_session(session_id, sponsors_found=sponsors_found, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

                        if not is_delegate(crt):
                            non_delegates += 1
                            _log_event(session_id, "non_delegate",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name, contact_url=contact_url,
                                       reason=f"Non-delegate: {crt or '(empty)'}")
                            update_scrape_session(session_id, non_delegates=non_delegates, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

                        warnings_raw = contact.get("warnings_raw", "")
                        detected = detect_warnings(warnings_raw)
                        should_skip, skip_reason = warning_is_excluded(detected, config.get("warning_exclusions", set()))

                        if should_skip:
                            skipped += 1
                            _log_event(session_id, "warning_skip",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name, warnings=warnings_raw,
                                       reason=skip_reason)
                            update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

                        mode = config["mode"]
                        best_phone, phone_label, ddi = resolve_best_phone(
                            contact, config.get("phone_region", "all"), mode
                        )

                        if mode in ("mobile", "full") and not best_phone:
                            skipped += 1
                            _log_event(session_id, "skipped",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name, reason=f"No valid phone for mode '{mode}'")
                            update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

                        if mode == "email" and not contact.get("email"):
                            skipped += 1
                            _log_event(session_id, "skipped",
                                       first_name=contact.get("first_name", ""),
                                       last_name=contact.get("last_name", ""),
                                       company=contact.get("company", ""),
                                       record_name=rec_name, reason="No email found")
                            update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                            continue

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

                        update_scrape_session(session_id,
                                        contacts_saved=contacts_saved,
                                        ddi_saved=ddi_saved,
                                        orders_done=orders_done,
                                        current_entity_index=rec_idx, 
                                        current_order_index=ord_idx+1)

                    except SkipEventSignal:
                        _log_event(session_id, "skipped", record_name=rec_name, reason="Skipped by signal")
                        skipped += 1
                        update_scrape_session(session_id, skipped=skipped, current_entity_index=rec_idx, current_order_index=ord_idx+1)
                        break
                    except Exception as e:
                        _log_event(session_id, "error", record_name=rec_name, reason=f"Error: {str(e)[:200]}")
                        # Don't fail the whole mission for one bad order, skip and record cursor advance
                        update_scrape_session(session_id, current_entity_index=rec_idx, current_order_index=ord_idx+1)

                    time.sleep(BETWEEN_ORDERS)
                
                # Finished all orders in this entity, reset start_order so the next entity starts at 0
                start_order = 0
                update_scrape_session(session_id, current_entity_index=rec_idx+1, current_order_index=0)

        # Finished run
        _log_event(session_id, "status", reason=f"Scrape completed — {contacts_saved} contacts saved")
        update_scrape_session(session_id,
                        status="completed",
                        finished_at=datetime.now().isoformat())

    except Exception as e:
        _log_event(session_id, "error", reason=f"Fatal: {str(e)[:300]}")
        update_scrape_session(session_id, status="error", finished_at=datetime.now().isoformat())
        import traceback
        traceback.print_exc()
        raise e
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    daemon_loop()
