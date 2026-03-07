"""
src/filters.py — Record filtering: keywords, regions, warnings, ZMTBE.
"""

import re
from config import ZMTBE_TOKEN


# ─── Warning exclusion system ────────────────────────────────────────────────

_CATEGORY_SIGNALS = {
    "dnc": [
        re.compile(r"\bdnc\b",                re.I),
        re.compile(r"\bdo\s+not\s+contact\b", re.I),
        re.compile(r"\bdo\s+not\s+email\b",   re.I),
    ],
    "open_opportunity": [
        re.compile(r"\bopen\s+opp\b",         re.I),
        re.compile(r"\bopen\s+opportunity\b", re.I),
    ],
    "do_not_email": [
        re.compile(r"\bdo\s+not\s+email\b",   re.I),
    ],
    "blacklist": [
        re.compile(r"\bblacklist(ed)?\b",     re.I),
    ],
    "yellow_card": [
        re.compile(r"\byellow\s*card\b",      re.I),
    ],
}

_CATEGORY_LABELS = {
    "dnc":              "DNC  (also catches: Do Not Contact + Do Not Email)",
    "open_opportunity": "Open Opportunity",
    "do_not_email":     "Do Not Email only  (narrow — email flag only)",
    "blacklist":        "Blacklist / Blacklisted",
    "yellow_card":      "Yellow Card",
    "__ALL__":          "All Warnings  (skip any contact with any warning at all)",
}


def warning_is_excluded(warnings_text: str, excluded: set) -> tuple:
    if not excluded:
        return False, ""
    w = (warnings_text or "").strip()
    if not w:
        return False, ""
    if "__ALL__" in excluded:
        return True, "All Warnings"
    for cat in excluded:
        for pattern in _CATEGORY_SIGNALS.get(cat, []):
            if pattern.search(w):
                return True, _CATEGORY_LABELS.get(cat, cat)
    return False, ""


def describe_exclusions(excluded: set) -> str:
    if not excluded:
        return "none"
    if "__ALL__" in excluded:
        return "ALL WARNINGS"
    return " | ".join(_CATEGORY_LABELS.get(c, c) for c in sorted(excluded))


# ─── ZMTBE filter ────────────────────────────────────────────────────────────

def is_zmtbe(name: str) -> bool:
    return ZMTBE_TOKEN in (name or "").lower()


# ─── Location / region tokens ───────────────────────────────────────────────

_LOCATION_TOKENS = {
    "global", "international", "virtual",
    "america", "americas", "north america", "na", "latam", "latin america",
    "canada", "us", "usa", "united states",
    "europe", "european", "emea", "dach", "benelux", "nordics",
    "uk", "united kingdom", "great britain",
    "germany", "german", "france", "french", "netherlands", "dutch",
    "belgium", "belgian", "spain", "spanish", "italy", "italian",
    "sweden", "swedish", "norway", "norwegian", "denmark", "danish",
    "finland", "finnish", "switzerland", "swiss", "austria", "austrian",
    "portugal", "portuguese", "ireland", "irish", "poland", "polish",
    "czech", "hungary", "hungarian", "greece", "greek",
    "apac", "asia", "australia", "australian", "singapore", "japan",
    "japanese", "india", "indian", "china", "chinese",
    "mea", "middle east", "africa", "african",
    "new york", "nyc", "london", "paris", "berlin", "sydney", "dubai",
    "toronto", "chicago", "dallas", "atlanta", "miami", "boston",
    "seattle", "san francisco", "las vegas", "denver", "nashville",
    "austin", "manchester", "edinburgh", "amsterdam", "munich",
    "digital", "live",
}

RECORD_REGION_SYNONYMS = {
    "all":           set(),
    "europe":        {"europe", "european", "eu", "emea", "dach", "benelux", "nordics"},
    "uk":            {"uk", "united kingdom", "great britain", "britain",
                      "london", "manchester", "birmingham", "edinburgh", "glasgow"},
    "us":            {"us", "usa", "united states", "america", "north america", "na"},
    "north_america": {"north america", "na", "america", "united states", "usa", "us", "canada"},
    "apac":          {"apac", "asia", "australia", "singapore", "japan", "india"},
    "mea":           {"mea", "middle east", "africa"},
    "digital":       {"digital", "live"},
}

_NORM = lambda s: re.sub(r"\s+", " ", (s or "").lower().strip())
_EDITION_RE = re.compile(r"(?<![0-9])\b([1-9][0-9]?)\b(?![0-9])")


def name_has_location(name: str) -> str:
    n = _NORM(name)
    for token in sorted(_LOCATION_TOKENS, key=len, reverse=True):
        if token in n:
            return token
    return ""


def name_has_edition_number(name: str) -> str:
    m = _EDITION_RE.search(name or "")
    return m.group(1) if m else ""


def _contains_any_token(text: str, tokens: set) -> bool:
    t = _NORM(text)
    for tok in sorted(tokens, key=len, reverse=True):
        if tok and tok in t:
            return True
    return False


def expand_keyword_targets(base_keywords: list, record_region: str) -> list:
    targets = []
    rr = (record_region or "all").lower().strip()
    region_tokens = RECORD_REGION_SYNONYMS.get(rr, set())
    for kw in base_keywords:
        kw_n = _NORM(kw)
        if not kw_n:
            continue
        targets.append(kw_n)
        for rt in region_tokens:
            rt_n = _NORM(rt)
            if rt_n:
                targets.append(f"{kw_n} {rt_n}")
                targets.append(f"{rt_n} {kw_n}")
    out, seen = [], set()
    for t in targets:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out


def record_passes_filter(name: str, base_keywords: list, record_region: str) -> tuple:
    n  = _NORM(name)
    ed = name_has_edition_number(name)
    if not ed:
        return False, "no 1-2 digit edition number found"
    loc = name_has_location(name)
    if not loc:
        return False, "no location/region found in name"
    rr = (record_region or "all").lower().strip()
    rr_tokens = RECORD_REGION_SYNONYMS.get(rr, set())
    if rr != "all" and not _contains_any_token(name, rr_tokens):
        return False, f"outside record-region ({rr})"
    base_kws = [k for k in (base_keywords or []) if _NORM(k)]
    if not base_kws:
        return False, "no keywords provided"
    targets = expand_keyword_targets(base_kws, rr)
    matched_target = next((t for t in targets if t in n), None)
    if not matched_target:
        matched_base = next((k for k in base_kws if _NORM(k) in n), None)
        if matched_base and (rr == "all" or _contains_any_token(name, rr_tokens)):
            matched_target = f"{_NORM(matched_base)} + region-signal"
        else:
            short = ", ".join(targets[:6]) + ("..." if len(targets) > 6 else "")
            return False, f"no keyword match (targets: {short})"
    return True, f"match='{matched_target}'  region='{rr}'  location='{loc}'  edition='{ed}'"
