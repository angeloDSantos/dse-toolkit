"""
src/filters.py — Record filtering, warning detection, sponsor/delegate checks (spec §6-7).
"""

import re

# ─── Region expansion (spec §5.3) ──────────────────────────────────────────

REGION_TOKENS = {
    "europe": ["europe", "eu", "emea", "dach", "nordics"],
    "uk":     ["uk", "united kingdom", "britain"],
    "us":     ["us", "usa", "united states", "america"],
    "north_america": ["us", "usa", "canada", "north america", "na"],
    "apac":   ["apac", "asia", "pacific", "asia pacific"],
    "mea":    ["mea", "middle east", "africa"],
    "digital": ["digital", "live", "virtual", "online"],
    "all":    [],
}

# Known location tokens that confirm a record is a real event
LOCATION_TOKENS = {
    "europe", "eu", "emea", "dach", "nordics", "uk", "us", "usa",
    "north america", "na", "apac", "asia", "pacific", "mea",
    "amsterdam", "london", "new york", "berlin", "paris", "barcelona",
    "dubai", "singapore", "sydney", "toronto", "chicago", "dallas",
    "atlanta", "miami", "san francisco", "los angeles", "boston",
    "seattle", "denver", "phoenix", "houston", "minneapolis",
    "digital", "live", "virtual", "online", "frankfurt", "munich",
    "zurich", "geneva", "madrid", "rome", "milan", "lisbon",
    "stockholm", "oslo", "copenhagen", "helsinki", "dublin",
    "brussels", "vienna", "prague", "warsaw", "budapest",
    "noordwijk", "cape town", "johannesburg",
}


def expand_keywords(keywords: list, region: str) -> list:
    """Expand each keyword with region variants for matching."""
    tokens = REGION_TOKENS.get(region, [])
    expanded = []
    for kw in keywords:
        kw_lower = kw.lower()
        expanded.append(kw_lower)
        for tok in tokens:
            expanded.append(f"{kw_lower} {tok}")
            expanded.append(f"{tok} {kw_lower}")
    return expanded


def _has_edition_number(name: str) -> bool:
    """Check for a 1 or 2 digit edition number in the record name."""
    return bool(re.search(r'\b\d{1,2}\b', name))


def _has_location_token(name: str) -> bool:
    """Check for a recognisable location or region token."""
    name_lower = name.lower()
    return any(tok in name_lower for tok in LOCATION_TOKENS)


def record_passes_filter(name: str, expanded_keywords: list, mode: str) -> bool:
    """Check if a record name passes the keyword and structural filters.

    For ALL SCRAPE mode, everything passes (except ZMTBE, handled separately).
    For filtered modes (1-3), requires edition number + location + keyword match.
    """
    if mode == "all":
        return True

    name_lower = name.lower()

    # Structural checks
    if not _has_edition_number(name):
        print(f"  FILTER SKIP: '{name}' — no edition number found")
        return False

    if not _has_location_token(name):
        print(f"  FILTER SKIP: '{name}' — no location token found")
        return False

    # Keyword match — OR logic, substring-based, case-insensitive
    return any(kw in name_lower for kw in expanded_keywords)


def is_zmtbe(name: str) -> bool:
    """Check if a record name contains 'zmtbe' (spec §12.3)."""
    from config import ZMTBE_TOKEN
    return ZMTBE_TOKEN in name.lower()


# ─── Warning detection (spec §6.5) ────────────────────────────────────────

# Maps raw text patterns to normalised category keys
_WARNING_PATTERNS = {
    "dnc": [
        r'\bdnc\b', r'\bdo\s*not\s*contact\b',
    ],
    "do_not_email": [
        r'\bdo\s*not\s*email\b', r'\bemail\s*opt[\s-]*out\b', r'\bdne\b',
    ],
    "do_not_text": [
        r'\bdo\s*not\s*text\b', r'\bsms\s*opt[\s-]*out\b', r'\bno\s*text\b',
    ],
    "blacklist": [
        r'\bblacklist(?:ed)?\b', r'\bcontact\s+is\s+blacklisted\b',
        r'\bthis\s+contact\s+is\s+blacklisted\b',
    ],
    "open_opportunity": [
        r'\bopen\s*opp(?:ortunity)?\b',
    ],
    "yellow_card": [
        r'\byellow\s*card\b',
    ],
}

# Compiled patterns for speed
_COMPILED_PATTERNS = {}
for cat, patterns in _WARNING_PATTERNS.items():
    _COMPILED_PATTERNS[cat] = [re.compile(p, re.IGNORECASE) for p in patterns]


_CATEGORY_LABELS = {
    "dnc": "DNC / Do Not Contact",
    "do_not_email": "Do Not Email",
    "do_not_text": "Do Not Text",
    "blacklist": "Blacklist",
    "open_opportunity": "Open Opportunity",
    "yellow_card": "Yellow Card",
    "__ALL__": "All Warnings",
}


def detect_warnings(raw_text: str) -> set:
    """Parse raw warning text into a set of normalised category keys."""
    detected = set()
    if not raw_text or not raw_text.strip():
        return detected

    for cat, compiled in _COMPILED_PATTERNS.items():
        for pattern in compiled:
            if pattern.search(raw_text):
                detected.add(cat)
                break  # One match per category is enough

    # Special case: "IMPORTANT" near "blacklisted"
    if "blacklist" not in detected:
        lower = raw_text.lower()
        if "important" in lower and "blacklisted" in lower:
            detected.add("blacklist")

    return detected


def warning_is_excluded(detected_warnings: set, exclusion_set: set) -> tuple:
    """Check if any detected warning matches the active exclusion set.

    Returns (should_skip: bool, reason: str)
    """
    if not detected_warnings or not exclusion_set:
        return False, ""

    # __ALL__ overrides everything
    if "__ALL__" in exclusion_set:
        reason = f"All Warnings (detected: {', '.join(sorted(detected_warnings))})"
        return True, reason

    # Check each detected warning against exclusion set
    for cat in detected_warnings:
        if cat in exclusion_set:
            label = _CATEGORY_LABELS.get(cat, cat)
            return True, label

    return False, ""


def describe_exclusions(exclusions: set) -> str:
    """Format exclusion set for display."""
    if not exclusions:
        return "(none)"
    labels = [_CATEGORY_LABELS.get(x, x) for x in sorted(exclusions)]
    return " | ".join(labels)


# ─── Sponsor and delegate checks (spec §7) ───────────────────────────────

def is_sponsor(contact_record_type: str, record_type: str) -> bool:
    """Check if either field contains 'Sponsor' (spec §7.1)."""
    for field in (contact_record_type, record_type):
        if field and "sponsor" in field.lower():
            return True
    return False


def is_delegate(contact_record_type: str) -> bool:
    """Check if Contact Record Type is 'Delegate' (spec §7.2)."""
    if not contact_record_type:
        return False
    return contact_record_type.strip().lower() == "delegate"
