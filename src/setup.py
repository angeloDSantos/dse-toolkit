"""
src/setup.py — Interactive setup flow for the scraper.

Implements the full 10-step setup (spec §3.1), multi-keyword entry (§5),
multi-warning exclusion with DNC sub-menu (§6), and confirmation summary (§3.3).
"""

from datetime import datetime


# ─── Helpers ────────────────────────────────────────────────────────────────

def _ask_choice(prompt: str, options: dict, allow_empty: bool = False):
    """Single-select menu. options = {"1": ("label", "value"), ...}"""
    print(f"\n{prompt}")
    for key in sorted(options.keys(), key=lambda k: int(k) if k.isdigit() else k):
        label, _ = options[key]
        print(f"  {key}) {label}")
    while True:
        ans = input("  → ").strip()
        if ans in options:
            label, value = options[ans]
            print(f"  ✓ {label}")
            return value
        if allow_empty and not ans:
            return None
        print(f"  Enter one of: {', '.join(sorted(options, key=lambda k: int(k) if k.isdigit() else k))}")


def _yn(prompt: str) -> bool:
    while True:
        ans = input(f"\n{prompt} (y/n): ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


# ─── Step 1: Scrape name ───────────────────────────────────────────────────

def ask_scrape_name() -> str:
    print("\n  Enter a name for this scrape (e.g. Amsterdam_CMO_April):")
    while True:
        name = input("  > ").strip()
        if name:
            return name
        print("  Scrape name cannot be empty.")


# ─── Step 2: Entity type ──────────────────────────────────────────────────

def ask_entity_type() -> str:
    return _ask_choice(
        "What do you want to scrape?",
        {
            "1": ("Events only", "events"),
            "2": ("Campaigns only", "campaigns"),
            "3": ("Both — Events first, then Campaigns", "both"),
        }
    )


# ─── Step 3: List scroll limit ────────────────────────────────────────────

def ask_list_limit() -> int:
    print("\n  How many records to collect from the list?")
    print("  (0 = no limit, press Enter for default 500):")
    while True:
        raw = input("  > ").strip()
        if not raw:
            print("  ✓ Default: 500")
            return 500
        try:
            v = int(raw)
            if v >= 0:
                print(f"  ✓ Limit: {v}" if v > 0 else "  ✓ No limit")
                return v
        except ValueError:
            pass
        print("  Enter a number (0 = no limit, blank = 500)")


# ─── Step 4: Keywords (multi-value, spec §5.2) ────────────────────────────

def collect_keywords() -> list:
    """Multi-keyword entry with running list and 'add another?' loop."""
    print()
    print("  KEYWORD SETUP")
    print("  " + "-" * 55)
    print("  Enter keywords one at a time. Type 'done' when finished.")
    print("  Examples: CMO, CIO, Marketing, AI Innovation, Digital")
    print()

    keywords = []
    while True:
        num = len(keywords) + 1
        raw = input(f"  Keyword {num}:\n  > ").strip()

        if raw.lower() == "done" and keywords:
            break
        if not raw:
            if keywords:
                break
            print("  At least one keyword is required.")
            continue

        # Support comma-separated input
        if "," in raw:
            parts = [k.strip() for k in raw.split(",") if k.strip()]
            keywords.extend(parts)
            print(f"  Added: {', '.join(parts)}")
        else:
            keywords.append(raw)
            print(f"  Added: '{raw}'")

        print(f"\n  Current keywords: {', '.join(keywords)}")
        if not _yn("  Add another keyword?"):
            break

    print(f"\n  Keywords confirmed:")
    for kw in keywords:
        print(f"    - {kw}")

    return keywords


# ─── Step 5: Record region ────────────────────────────────────────────────

def ask_record_region() -> str:
    return _ask_choice(
        "Which record region to scrape?",
        {
            "1": ("Europe (EU / EMEA / DACH / Nordics)", "europe"),
            "2": ("UK", "uk"),
            "3": ("US", "us"),
            "4": ("North America (US + Canada)", "north_america"),
            "5": ("APAC", "apac"),
            "6": ("MEA", "mea"),
            "7": ("Digital / Live", "digital"),
            "8": ("All regions", "all"),
        }
    )


# ─── Step 6: Collection mode ─────────────────────────────────────────────

def ask_collection_mode() -> str:
    return _ask_choice(
        "What data to collect?",
        {
            "1": ("Mobile only   — name, company, phone", "mobile"),
            "2": ("Email only    — name, company, email", "email"),
            "3": ("Full          — name, company, phone, email, title", "full"),
            "4": ("ALL SCRAPE    — all fields including secondary email (no keyword filter)", "all"),
        }
    )


# ─── Step 7: Warning exclusions (multi-select, spec §6.2–6.3) ────────────

def _collect_dnc_subtypes() -> set:
    """DNC sub-menu (spec §6.3)."""
    print()
    print("  DNC SUB-MENU — which DNC-related warning types to exclude?")
    print("  " + "-" * 55)

    selected = set()
    options = {
        "1": ("DNC / Do Not Contact", "dnc"),
        "2": ("Do Not Email", "do_not_email"),
        "3": ("Do Not Text / SMS opt-out", "do_not_text"),
        "4": ("All DNC types", "__ALL_DNC__"),
        "5": ("Done", "__DONE__"),
    }

    while True:
        print(f"\n  Currently selected: {', '.join(sorted(selected)) or '(none)'}")
        for key, (label, _) in sorted(options.items()):
            print(f"    {key}) {label}")

        ans = input("\n  Enter choice:\n  > ").strip()
        if ans not in options:
            print(f"  Enter one of: {', '.join(sorted(options))}")
            continue

        _, value = options[ans]

        if value == "__DONE__":
            break
        elif value == "__ALL_DNC__":
            selected = {"dnc", "do_not_email", "do_not_text"}
            print("  Added: All DNC types")
            break
        else:
            selected.add(value)
            label, _ = options[ans]
            print(f"  Added: {label}")
            if not _yn("  Add another DNC sub-type?"):
                break

    display = " | ".join(sorted(selected)) or "(none)"
    print(f"\n  DNC sub-types confirmed: {display}")
    print("  Returning to warning menu.")
    return selected


def collect_warning_exclusions() -> set:
    """Multi-select warning exclusion menu (spec §6.2)."""
    print()
    print("  WARNING EXCLUSIONS — which contacts should be SKIPPED?")
    print("  " + "-" * 55)

    selected = set()

    while True:
        print(f"\n  Currently selected: {', '.join(sorted(selected)) or '(none)'}")
        print("    1) DNC             (opens DNC sub-type menu)")
        print("    2) Blacklist")
        print("    3) Open Opportunity")
        print("    4) Yellow Card")
        print("    5) All Warnings    (skip any contact with any warning at all)")
        print("    6) Done / None")

        ans = input("\n  Enter choice:\n  > ").strip()

        if ans == "1":
            dnc_types = _collect_dnc_subtypes()
            selected.update(dnc_types)

        elif ans == "2":
            selected.add("blacklist")
            print("  Added: Blacklist")

        elif ans == "3":
            selected.add("open_opportunity")
            print("  Added: Open Opportunity")

        elif ans == "4":
            selected.add("yellow_card")
            print("  Added: Yellow Card")

        elif ans == "5":
            selected = {"__ALL__"}
            print("  ✓ All Warnings — any warned contact will be skipped")
            break

        elif ans == "6":
            break
        else:
            print("  Enter 1–6")
            continue

        if ans != "1":  # DNC sub-menu already asked
            if not _yn("  Add another warning exclusion?"):
                break

    display = ", ".join(sorted(selected)) or "(none)"
    print(f"\n  Warning exclusions confirmed:")
    for item in sorted(selected):
        print(f"    - {item}")

    return selected


# ─── Step 8: Phone region ────────────────────────────────────────────────

def ask_phone_region() -> str:
    return _ask_choice(
        "Which phone region?",
        {
            "1": ("UK only", "uk"),
            "2": ("US only", "us"),
            "3": ("Europe only", "europe"),
            "4": ("All regions", "all"),
        }
    )


# ─── Step 9: Confirmation summary (spec §3.3) ───────────────────────────

LABEL_MAP = {
    "dnc": "DNC / Do Not Contact",
    "do_not_email": "Do Not Email",
    "do_not_text": "Do Not Text",
    "blacklist": "Blacklist",
    "open_opportunity": "Open Opportunity",
    "yellow_card": "Yellow Card",
    "__ALL__": "All Warnings",
}


def print_confirmation_summary(config: dict):
    """Print a formatted summary matching spec §3.3."""
    dnc_types = {"dnc", "do_not_email", "do_not_text"}
    active_dnc = config.get("warning_exclusions", set()) & dnc_types

    print()
    print("=" * 65)
    print("  SCRAPE SUMMARY — please confirm before starting")
    print("=" * 65)
    print(f"  Scrape name       : {config['scrape_name']}")
    print(f"  Run timestamp     : {config['run_timestamp']}")
    print(f"  Output folder     : {config['output_folder']}")
    print(f"  Entity type       : {config['entity_type'].title()}")
    print(f"  List limit        : {config.get('list_limit', 500)}")
    print(f"  Run mode          : {config.get('run_mode', 'fresh').title()} run")
    print()

    keywords = config.get("keywords", [])
    if keywords:
        print("  Keywords:")
        for kw in keywords:
            print(f"    - {kw}")
    else:
        print("  Keywords          : (ALL SCRAPE — no filter)")

    print()
    print(f"  Record region     : {config.get('record_region', 'all').title()}")
    print(f"  Collection mode   : {config.get('mode', 'mobile').title()}")

    if config.get("mode") in ("mobile", "full"):
        print(f"  Phone region      : {config.get('phone_region', 'all').title()}")

    print()
    excl = config.get("warning_exclusions", set())
    if excl:
        print("  Warning exclusions:")
        for item in sorted(excl):
            print(f"    - {LABEL_MAP.get(item, item)}")
    else:
        print("  Warning exclusions: (none)")

    if active_dnc:
        print()
        print("  DNC sub-types active:")
        for item in sorted(active_dnc):
            print(f"    - {LABEL_MAP.get(item, item)}")

    print("=" * 65)
    print()


# ─── Full setup flow ────────────────────────────────────────────────────────

def run_full_setup() -> dict:
    """Run the complete 10-step setup flow. Returns config dict."""
    print()
    print("=" * 55)
    print("  NEW SCRAPE SETUP")
    print("=" * 55)

    # Step 1
    scrape_name = ask_scrape_name()

    # Step 2
    entity_type = ask_entity_type()

    # Step 3
    list_limit = ask_list_limit()

    # Step 4 — keywords (skip for ALL SCRAPE)
    mode = ask_collection_mode()  # Step 6 moved up so we know if ALL SCRAPE
    if mode == "all":
        keywords = []
        print("\n  ALL SCRAPE mode — keyword filter disabled.")
    else:
        keywords = collect_keywords()

    # Step 5
    record_region = ask_record_region()

    # Step 7
    warning_exclusions = collect_warning_exclusions()

    # Step 8 — phone region (only for mobile/full modes)
    phone_region = "all"
    if mode in ("mobile", "full"):
        phone_region = ask_phone_region()

    # Build config
    run_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    from config import SCRAPES_ROOT
    import os
    output_folder = os.path.join(SCRAPES_ROOT, scrape_name, run_ts)

    config = {
        "scrape_name": scrape_name,
        "run_timestamp": run_ts,
        "output_folder": output_folder,
        "entity_type": entity_type,
        "mode": mode,
        "keywords": keywords,
        "record_region": record_region,
        "phone_region": phone_region,
        "warning_exclusions": warning_exclusions,
        "list_limit": list_limit,
        "run_mode": "fresh",
        "stopped_cleanly": False,
        "completed_record_urls": [],
    }

    # Step 9 — confirmation summary
    print_confirmation_summary(config)

    # Step 10 — explicit confirmation
    if not _yn("  Ready to start scraping. Confirm?"):
        print("  Setup cancelled. Returning to Step 1.\n")
        return run_full_setup()  # Restart from beginning

    # Create output folder
    os.makedirs(output_folder, exist_ok=True)

    return config
