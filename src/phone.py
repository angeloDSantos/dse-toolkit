"""
src/phone.py — Phone normalisation, junk filtering, region & mobile validation.

Ported from the battle-tested logic in bulk_scraper.py with support
for UK, US, FR, DE, ES, IT, NL, BE, SE, NO, CH, AT, DK, IE, PT, FI, LU, GR.
"""

import re

_digits = lambda s: re.sub(r"\D", "", s or "")


# ─── Normalise ──────────────────────────────────────────────────────────────

def normalise_phone(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"(?i)\s*(ext|extn|extension|x|#)\.?\s*\d{1,6}\s*$", "", raw).strip()
    cleaned = re.sub(r"[\s\-\.\(\)\/\\]", "", raw)
    cleaned = re.sub(r"^\+44\(0\)", "+44", cleaned)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    d = _digits(cleaned)
    if not d:
        return ""
    if cleaned.startswith("+"):
        return "+" + _digits(cleaned[1:])
    if len(d) == 11 and d.startswith("07"):
        return "+44" + d[1:]
    if len(d) == 10 and d.startswith("7"):
        return "+44" + d
    if len(d) == 10 and d[0] not in ("0", "7"):
        return "+1" + d
    if len(d) == 11 and d.startswith("1"):
        return "+" + d
    if len(d) == 10 and d.startswith("0"):
        return "+33" + d[1:]
    if len(d) == 9 and d[0] in ("6", "7"):
        return "+33" + d
    if len(d) in (10, 11, 12, 13) and d.startswith("0"):
        return "+49" + d[1:]
    if len(d) >= 11 and d.startswith("49"):
        return "+" + d
    if len(d) == 10 and d.startswith("3"):
        return "+39" + d
    if len(d) == 10 and d.startswith("06"):
        return "+31" + d[1:]
    if len(d) == 10 and d.startswith("04"):
        return "+32" + d[1:]
    if 7 <= len(d) <= 15:
        return d
    return ""


# ─── Junk filters ───────────────────────────────────────────────────────────

def _check_junk(n):
    if not n:
        return True, "empty"
    d = _digits(n)
    if len(d) < 7 or len(d) > 15:
        return True, f"wrong length ({len(d)})"
    if len(set(d)) == 1:
        return True, "all same digit"
    if re.search(r"(\d)\1{4,}", d):
        return True, "5+ repeated digits"
    if d.endswith("000"):
        return True, "ends in 000"
    asc, desc = "0123456789", "9876543210"
    for i in range(len(d) - 5):
        if d[i:i+6] in asc or d[i:i+6] in desc:
            return True, "sequential run"
    return False, ""


def _check_uk_nonmobile(n):
    if not n.startswith("+44"):
        return False, ""
    local = _digits(n[3:])
    for pfx in ("800", "808", "844", "845", "843", "870", "871",
                 "872", "873", "874", "875", "118", "500", "300"):
        if local.startswith(pfx):
            return True, f"UK non-geo (+44{pfx})"
    return False, ""


def _check_us_junk(n):
    if not n.startswith("+1"):
        return False, ""
    local = _digits(n[2:])
    if len(local) != 10:
        return True, "US wrong length"
    area, ex = local[:3], local[3:6]
    if area in ("800", "888", "877", "866", "855", "844", "833", "822"):
        return True, "US toll-free"
    if area == "900":
        return True, "US 900 premium"
    if ex == "555":
        return True, "US 555 fictitious"
    if area[0] in ("0", "1"):
        return True, f"US invalid area ({area})"
    return False, ""


# ─── Region check ───────────────────────────────────────────────────────────

_REGION_PREFIXES = {
    "uk":     ("+44",),
    "us":     ("+1",),
    "europe": ("+33", "+34", "+39", "+31", "+32", "+41", "+43",
               "+45", "+46", "+47", "+353", "+351", "+30", "+358", "+352", "+354", "+49"),
    "all":    None,
}


def _check_region(n, region):
    prefixes = _REGION_PREFIXES.get(region)
    if prefixes is None:
        return True, ""
    if any(n.startswith(p) for p in prefixes):
        return True, ""
    return False, f"outside region ({region}): {n}"


# ─── Mobile check ───────────────────────────────────────────────────────────

def _check_mobile(n, from_mobile_field):
    checks = [
        ("+44", lambda l: l.startswith("7") and len(l) == 10, "UK mobile",  "UK not +447"),
        ("+33", lambda l: l[0] in ("6", "7"),                "FR mobile",  "FR not mobile"),
        ("+49", lambda l: l[:2] in ("15", "16", "17"),       "DE mobile",  "DE not mobile"),
        ("+34", lambda l: l[0] in ("6", "7"),                "ES mobile",  "ES not mobile"),
        ("+39", lambda l: l.startswith("3"),                 "IT mobile",  "IT not mobile"),
        ("+31", lambda l: l.startswith("6"),                 "NL mobile",  "NL not mobile"),
        ("+32", lambda l: l.startswith("4"),                 "BE mobile",  "BE not mobile"),
        ("+46", lambda l: l.startswith("7"),                 "SE mobile",  "SE not mobile"),
        ("+47", lambda l: l[0] in ("4", "9"),                "NO mobile",  "NO not mobile"),
        ("+41", lambda l: l.startswith("7"),                 "CH mobile",  "CH not mobile"),
        ("+43", lambda l: l.startswith("6"),                 "AT mobile",  "AT not mobile"),
        ("+45", lambda l: l[0] in ("2", "3", "4", "5", "6"), "DK mobile", "DK not mobile"),
    ]
    for cc, test, ok, fail in checks:
        if n.startswith(cc):
            return (True, ok) if test(_digits(n[len(cc):])) else (False, fail)

    four = [
        ("+353", lambda l: l.startswith("8"),  "IE mobile", "IE not mobile"),
        ("+351", lambda l: l.startswith("9"),  "PT mobile", "PT not mobile"),
        ("+358", lambda l: l[0] in ("4", "5"), "FI mobile", "FI not mobile"),
        ("+352", lambda l: l.startswith("6"),  "LU mobile", "LU not mobile"),
        ("+30",  lambda l: l.startswith("69"), "GR mobile", "GR not mobile"),
    ]
    for cc, test, ok, fail in four:
        if n.startswith(cc):
            return (True, ok) if test(_digits(n[len(cc):])) else (False, fail)

    if n.startswith("+1"):
        return (True, "US/CA Mobile field") if from_mobile_field else (True, "US/CA Phone field")
    return False, "unrecognised prefix"


# ─── Public API ──────────────────────────────────────────────────────────────

def validate_phone(raw, from_mobile_field, region, for_ddi=False):
    """
    Returns (normalised_number, is_valid, note).
    """
    n = normalise_phone(raw)
    for bad, reason in [_check_junk(n), _check_uk_nonmobile(n), _check_us_junk(n)]:
        if bad:
            return n, False, f"REJECT: {reason}"
    in_r, reason = _check_region(n, region)
    if not in_r:
        return n, False, f"REJECT: {reason}"
    if for_ddi:
        return n, True, "DDI: valid"
    is_mob, note = _check_mobile(n, from_mobile_field)
    if not is_mob:
        return n, False, f"REJECT: {note}"
    return n, True, note
