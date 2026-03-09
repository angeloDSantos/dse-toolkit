#!/usr/bin/env python3
"""
scraper.py — Unified Salesforce CRM Scraper
=============================================

One script to rule them all. Supports:
  • Filtered scrape   — keyword + region + entity type (events / campaigns / both)
  • ALL SCRAPE        — every event + campaign, delegates only
  • Resume            — pick up a previous session folder
  • Parallel workers  — 1-6 Selenium instances for speed
  • Summit loop       — browsers stay alive, scrape multiple summits

Usage:
    python scraper.py
"""

import os
import sys
import re
import time
from datetime import datetime

# Add project root to path so imports work from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    SF_BASE, SF_EVENTS_URL, SF_CAMPAIGNS_URL,
    CHROME_PROFILE_DIR, SIGNAL_FILE,
    DEFAULT_WORKERS, MAX_WORKERS,
)
from src.driver import create_driver, settle
from src.signals import Controls, SkipEventSignal, QuitSignal
from src.navigation import collect_list_records, safe_stem
from src.filters import (
    record_passes_filter, warning_is_excluded, describe_exclusions,
    is_zmtbe, _CATEGORY_LABELS,
)
from src.parallel import run_parallel, SessionStats


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ask(prompt: str, choices: dict) -> str:
    print(f"\n{prompt}")
    for key, (label, _) in sorted(choices.items()):
        print(f"  {key}) {label}")
    while True:
        ans = input("  → ").strip()
        if ans in choices:
            label, value = choices[ans]
            print(f"  ✓ {label}\n")
            return value
        print(f"  Enter one of: {', '.join(sorted(choices))}")


def _ask_int(prompt: str, default: int = 1, min_v: int = 1, max_v: int = 99) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default
        try:
            v = int(raw)
            if min_v <= v <= max_v:
                return v
        except ValueError:
            pass
        print(f"  Enter a number {min_v}–{max_v} (default {default})")


def _yn(prompt: str) -> bool:
    while True:
        ans = input(f"\n{prompt} (y/n): ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def _collect_keywords() -> list:
    keywords = []
    
    # Force at least one keyword
    while True:
        first_kw = input("\n  Enter keyword (e.g., CMO Europe): ").strip()
        if first_kw:
            keywords.append(first_kw)
            break
        print("  At least one keyword is required.")
        
    # Ask for additional keywords
    while True:
        ans = input("  Add another keyword? (y/n / blank to finish): ").strip().lower()
        if ans in ("n", "no", ""):
            break
        elif ans in ("y", "yes"):
            kw = input("  Enter next keyword: ").strip()
            if kw:
                keywords.append(kw)
        else:
            # If they just typed another keyword instead of y/n, accept it as a keyword
            if ans:
                keywords.append(ans)
                
    return keywords


def _collect_warning_exclusions() -> set:
    cats = {
        "1": ("DNC / Do Not Contact / Do Not Email",   "dnc"),
        "2": ("Open Opportunity",                       "open_opportunity"),
        "3": ("Blacklist / Blacklisted",                "blacklist"),
        "4": ("Yellow Card",                            "yellow_card"),
        "5": ("ALL warnings (skip any warned contact)", "__ALL__"),
        "6": ("None — keep everything",                 "__NONE__"),
    }

    selected = set()

    while True:
        print("\n  Exclude contacts with which warnings?")
        for key, (label, val) in sorted(cats.items()):
            check = " ✓" if val in selected else ""
            print(f"  {key}) {label}{check}")
        if selected:
            print(f"\n  Currently selected: {describe_exclusions(selected)}")
        print(f"\n  d) Done selecting")

        ans = input("  → ").strip()

        if ans.lower() == "d":
            break

        if ans not in cats:
            print(f"  Enter one of: {', '.join(sorted(cats))}, or d to finish")
            continue

        _, value = cats[ans]

        if value == "__NONE__":
            print("  ✓ No exclusions — keeping everything.\n")
            return set()

        if value == "__ALL__":
            print("  ✓ ALL warnings selected — skipping any warned contact.\n")
            return {"__ALL__"}

        if value in selected:
            selected.discard(value)
            print(f"  ✗ Removed: {cats[ans][0]}")
        else:
            selected.add(value)
            print(f"  ✓ Added: {cats[ans][0]}")

    if selected:
        print(f"\n  Final exclusions: {describe_exclusions(selected)}\n")
    else:
        print("  No exclusions selected — keeping everything.\n")
    return selected


def _make_session_folder(label: str) -> str:
    ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe = re.sub(r"[^A-Za-z0-9_\- ]", "", label).strip().replace(" ", "_")
    name = f"scrape_{ts}_{safe}" if safe else f"scrape_{ts}"
    os.makedirs(name, exist_ok=True)
    return name


# ─── Session Config ──────────────────────────────────────────────────────────

def configure_session() -> dict:
    print()
    print("=" * 60)
    print("  SALESFORCE SCRAPER v2.0")
    print("=" * 60)

    scrape_mode = _ask("  Choose scrape type:", {
        "1": ("Filtered — events only  (keyword + region)",       "events_filtered"),
        "2": ("Filtered — campaigns only  (keyword + region)",    "campaigns_filtered"),
        "3": ("Filtered — both  (events + campaigns, filtered)",  "both_filtered"),
        "4": ("ALL SCRAPE — every event + campaign, delegates",   "all_scrape"),
    })

    # Workers
    n_workers = _ask_int(
        f"\n  Parallel browser windows? (1–{MAX_WORKERS}, default {DEFAULT_WORKERS}): ",
        default=DEFAULT_WORKERS, min_v=1, max_v=MAX_WORKERS,
    )
    print(f"  ✓ {n_workers} worker{'s' if n_workers > 1 else ''}\n")

    if scrape_mode == "all_scrape":
        print("  " + "-" * 56)
        print("  ALL SCRAPE — every event + campaign, delegates only")
        print("  ZMTBE excluded | Sponsors/non-delegates filtered")
        print("  " + "-" * 56)
        list_limit = _ask_int(
            "\n  List scroll limit per entity (0 = no limit): ",
            default=0, min_v=0, max_v=99999,
        )
        list_limit = None if list_limit == 0 else list_limit
        warning_exclusions = _collect_warning_exclusions()
        return {
            "scrape_mode":        "all_scrape",
            "entity":             "both",
            "list_limit":         list_limit,
            "keywords":           ["all_scrape"],
            "record_region":      "all",
            "mode":               "all",
            "phone_region":       "all",
            "warning_exclusions": warning_exclusions,
            "n_workers":          n_workers,
        }

    entity_map = {
        "events_filtered":    "events",
        "campaigns_filtered": "campaigns",
        "both_filtered":      "both",
    }
    entity = entity_map[scrape_mode]

    list_limit = _ask_int(
        "\n  List scroll limit (0 = no limit, default 500): ",
        default=500, min_v=0, max_v=99999,
    )
    list_limit = None if list_limit == 0 else list_limit

    keywords = _collect_keywords()

    record_region = _ask("  Which RECORD region to scrape?", {
        "1": ("Europe (EU / EMEA / DACH / Nordics)",    "europe"),
        "2": ("UK",                                     "uk"),
        "3": ("US",                                     "us"),
        "4": ("North America (US / Canada / NA)",       "north_america"),
        "5": ("APAC",                                   "apac"),
        "6": ("MEA",                                    "mea"),
        "7": ("Digital (digital OR live)",               "digital"),
        "8": ("All record regions",                      "all"),
    })

    mode = _ask("  What data to collect?", {
        "1": ("Mobile — name, company, phone",                "mobile"),
        "2": ("Email  — name, company, email",                "email"),
        "3": ("Full   — name, company, phone + email + title", "full"),
    })

    warning_exclusions = _collect_warning_exclusions()

    phone_region = "all"
    if mode in ("mobile", "full"):
        phone_region = _ask("  Phone region filter:", {
            "1": ("UK only",     "uk"),
            "2": ("US only",     "us"),
            "3": ("Europe only", "europe"),
            "4": ("All regions", "all"),
        })

    return {
        "scrape_mode":        scrape_mode,
        "entity":             entity,
        "list_limit":         list_limit,
        "keywords":           keywords,
        "record_region":      record_region,
        "mode":               mode,
        "phone_region":       phone_region,
        "warning_exclusions": warning_exclusions,
        "n_workers":          n_workers,
    }


# ─── Build filtered record list ─────────────────────────────────────────────

def _build_passed(all_records, cfg, entity_name):
    is_all = cfg["scrape_mode"] == "all_scrape"
    if is_all:
        passed = [(url, nm, "all-scrape") for url, nm in all_records]
        print(f"\n  ALL SCRAPE — {len(passed)} {entity_name} records queued\n")
        return passed

    print(f"\n  Filtering {len(all_records)} records...")
    print(f"  Keywords: {', '.join(repr(k) for k in cfg['keywords'])}")
    print(f"  Region  : {cfg['record_region']}\n")

    passed, rejected = [], []
    for url, raw_name in all_records:
        nm = (raw_name or "").strip()
        if not nm:
            passed.append((url, nm, "no name — included tentatively"))
            continue
        if is_zmtbe(nm):
            rejected.append((url, nm, "ZMTBE excluded"))
            continue
        ok, reason = record_passes_filter(nm, cfg["keywords"], cfg["record_region"])
        if ok:
            passed.append((url, nm, reason))
        else:
            rejected.append((url, nm, reason))

    print(f"  Matched {len(passed)} / {len(all_records)} for [{entity_name}]")
    for _, nm, reason in passed[:20]:
        display = nm[:60] + ("..." if len(nm) > 60 else "")
        print(f"  + {display}")
    if len(passed) > 20:
        print(f"  ... and {len(passed) - 20} more matched.")
    if not passed:
        print("  No records matched.\n")
    return passed


# ─── Signal instructions ────────────────────────────────────────────────────

def _print_signal_instructions():
    print()
    print("  ┌─────────────────────────────────────────────────┐")
    print("  │  Open a SECOND terminal and use signal.py:      │")
    print("  │    python signal.py s   → skip event/campaign   │")
    print("  │    python signal.py c   → skip contact          │")
    print("  │    python signal.py p   → pause / resume        │")
    print("  │    python signal.py r   → redo previous         │")
    print("  │    python signal.py q   → quit cleanly          │")
    print("  │    python signal.py x kw → exclude keyword      │")
    print("  └─────────────────────────────────────────────────┘")
    print()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    cfg = configure_session()
    n_workers = cfg["n_workers"]

    _print_signal_instructions()

    # Clear leftover signals
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)

    # Create Selenium drivers
    print(f"\n  Opening {n_workers} Chrome window{'s' if n_workers > 1 else ''}...")
    drivers = []
    for i in range(n_workers):
        d = create_driver(CHROME_PROFILE_DIR, worker_id=i + 1)
        d.get(SF_BASE)
        drivers.append(d)
        print(f"  ✓ Window {i + 1} opened")

    if n_workers == 1:
        print("\n  Log in to Salesforce in the Chrome window.")
    else:
        print(f"\n  Log in to Salesforce in ALL {n_workers} Chrome windows.")
        print("  Each window needs its own login (they use separate profiles).")
    print("  When ready, return here.")
    input("\n  Press ENTER to start scraping...\n")

    ctrl = Controls()

    # ── Summit loop — browser stays alive ────────────────────────────────────

    summit_count = 0
    while True:
        summit_count += 1
        folder   = _make_session_folder(
            "_".join(cfg["keywords"][:3]) if cfg["scrape_mode"] != "all_scrape" else "ALL"
        )
        session  = SessionStats()

        # Use first driver as the orchestrator for list collection
        orchestrator = drivers[0]

        def _process_entity(entity_name):
            url = SF_EVENTS_URL if entity_name == "events" else SF_CAMPAIGNS_URL
            orchestrator.get(url)
            settle(orchestrator, 2.0, ctrl)

            try:
                all_records = collect_list_records(
                    orchestrator, ctrl, entity_name, cfg["list_limit"])
            except SkipEventSignal:
                print(f"\n  [SKIP] Skipped {entity_name} list.\n")
                return
            except QuitSignal:
                return

            if not all_records:
                print(f"\n  No records found for {entity_name}.\n")
                return

            passed = _build_passed(all_records, cfg, entity_name)
            if not passed:
                return

            run_parallel(
                records=passed,
                drivers=drivers,
                ctrl=ctrl,
                mode=cfg["mode"],
                phone_region=cfg["phone_region"],
                folder=folder,
                warning_exclusions=cfg["warning_exclusions"],
                session_stats=session,
            )

        entity = cfg["entity"]
        if entity == "both":
            _process_entity("events")
            if not ctrl.quit_requested():
                _process_entity("campaigns")
        else:
            _process_entity(entity)

        # ── Session summary ──────────────────────────────────────────────────
        m, s = divmod(int(session.elapsed()), 60)
        h, m = divmod(m, 60)
        elapsed = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"

        print()
        print("=" * 60)
        print("  SESSION COMPLETE")
        print("=" * 60)
        print(f"  Output folder      : {os.path.abspath(folder)}/")
        print(f"  Workers used       : {n_workers}")
        print(f"  Time elapsed       : {elapsed}")
        print(f"  Warning exclusions : {describe_exclusions(cfg['warning_exclusions'])}")
        print(f"  Contacts saved     : {session.saved}")
        print(f"  DDI saved          : {session.ddi}")
        print(f"  Orders processed   : {session.orders}")
        print(f"  Sponsors found     : {session.sponsors}")
        print(f"  Non-delegates      : {session.non_delegates}")
        print(f"  Skipped            : {session.skipped}")
        if session.errors:
            print(f"  Errors             : {session.errors}")
        print("=" * 60)

        # ── Loop for next summit ─────────────────────────────────────────────
        if ctrl.quit_requested():
            break

        if not _yn("  Scrape another summit?"):
            break

        print("\n  Browser windows stay open — reusing sessions.\n")

    # Cleanup
    print("\n  Closing browsers...")
    for d in drivers:
        try:
            d.quit()
        except Exception:
            pass

    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)

    print("  Done. Goodbye!\n")


if __name__ == "__main__":
    main()
