"""
src/phone.py — Phone normalisation and regional validation (spec §11).

Complete 7-step normalisation pipeline, junk rejection, regional mobile
validation for 16+ countries, and decision logging.
"""

import re


# ─── Step 1-7: Normalisation pipeline (spec §11.2) ────────────────────────

def normalise_phone(raw: str) -> str:
    """Apply the full 7-step normalisation pipeline to a raw phone string.

    Returns the normalised E.164-style string (e.g. "+447700900123") or
    empty string if the input is empty/unusable.
    """
    if not raw or not raw.strip():
        return ""

    # Step 1 — Trim whitespace
    cleaned = raw.strip()

    # Step 2 — Strip extensions
    cleaned = re.sub(
        r'(?i)\s*(ext|extn|extension|x|#)\.?\s*\d{1,6}\s*$', '', cleaned
    )

    # Step 3 — Handle +44(0) pattern BEFORE removing parens
    cleaned = re.sub(r'^\+44\s*\(0\)', '+44', cleaned)

    # Step 4 — Remove formatting characters (spaces, hyphens, dots, parens, slashes)
    has_plus = cleaned.startswith("+")
    cleaned = re.sub(r'[\s\-\.\(\)/\\]', '', cleaned)
    if has_plus and not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    # Step 5 — Handle 00 international prefix
    if cleaned.startswith("00") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned[2:]

    # Step 6 — UK local 07... format
    digits = re.sub(r'[^\d]', '', cleaned)

    if not cleaned.startswith("+"):
        # 11 digits starting with 07 → UK mobile
        if len(digits) == 11 and digits.startswith("07"):
            return "+44" + digits[1:]

        # 10 digits starting with 7 → UK mobile (trunk already dropped)
        if len(digits) == 10 and digits.startswith("7"):
            return "+44" + digits

        # Step 7 — US/Canada 10-digit local format
        if len(digits) == 10 and digits[0] not in ("0", "7"):
            return "+1" + digits

        # 11 digits starting with 1 → US/Canada
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits

    return cleaned if cleaned.startswith("+") else ""


# ─── Junk rejection (spec §11.3) ──────────────────────────────────────────

def _is_junk(number: str) -> tuple:
    """Check if a normalised number is junk.
    Returns (is_junk: bool, reason: str).
    """
    digits = re.sub(r'[^\d]', '', number)

    # Too short
    if len(digits) < 7:
        return True, f"too short ({len(digits)} digits)"

    # Too long (E.164 max is 15)
    if len(digits) > 15:
        return True, f"too long ({len(digits)} digits, max 15)"

    # All digits identical
    if len(set(digits)) == 1:
        return True, "all digits identical"

    # 5+ consecutive identical digits
    for i in range(len(digits) - 4):
        if len(set(digits[i:i+5])) == 1:
            return True, f"5+ consecutive identical digits ({digits[i]})"

    # Ends in 000
    if digits.endswith("000"):
        return True, "ends in 000 (placeholder/switchboard)"

    # Sequential run of 6+
    for i in range(len(digits) - 5):
        seq = True
        for j in range(1, 6):
            if int(digits[i+j]) != int(digits[i]) + j:
                seq = False
                break
        if seq:
            return True, f"sequential run of 6+ digits starting at {digits[i:i+6]}"

    return False, ""


# ─── UK range rejection (spec §11.4) ──────────────────────────────────────

_UK_NON_MOBILE_PREFIXES = [
    "800", "808",               # Freephone
    "844", "845", "843",        # Service numbers
    "870", "871", "872", "873", "874", "875",  # Non-geographic
    "118",                      # Directory enquiries
    "500",                      # Corporate paging
    "300",                      # UK government
    "56", "55", "3",            # Business numbers, not mobile
]


def _uk_range_check(local_part: str) -> tuple:
    """Check UK number against non-mobile/non-geographic ranges.
    local_part is the digits after +44.
    Returns (is_rejected: bool, reason: str).
    """
    for prefix in _UK_NON_MOBILE_PREFIXES:
        if local_part.startswith(prefix):
            return True, f"UK non-geographic range +44{prefix}"
    return False, ""


# ─── US junk rejection (spec §11.5) ───────────────────────────────────────

_US_TOLL_FREE = {"800", "888", "877", "866", "855", "844", "833", "822"}


def _us_junk_check(local_part: str) -> tuple:
    """Check US/Canada number for junk patterns.
    local_part is the 10 digits after +1.
    Returns (is_rejected: bool, reason: str).
    """
    if len(local_part) != 10:
        return True, f"US local part is {len(local_part)} digits, expected 10"

    area_code = local_part[:3]

    # Invalid area codes
    if area_code[0] in ("0", "1"):
        return True, f"invalid US area code starting with {area_code[0]}"

    # Toll-free
    if area_code in _US_TOLL_FREE:
        return True, f"US toll-free area code {area_code}"

    # Premium
    if area_code == "900":
        return True, "US premium rate 900"

    # Fictional exchange (digits 4-6 = 555)
    exchange = local_part[3:6]
    if exchange == "555":
        return True, "US fictional exchange 555"

    return False, ""


# ─── Region inclusion check (spec §11.6) ──────────────────────────────────

_REGION_CODES = {
    "uk": ["+44"],
    "us": ["+1"],
    "europe": [
        "+33", "+34", "+39", "+31", "+32", "+41", "+43",
        "+45", "+46", "+47", "+353", "+351", "+30", "+358",
        "+352", "+354", "+49",
    ],
    "all": [],  # Accept any
}


def _check_region(number: str, phone_region: str) -> tuple:
    """Check if a number's country code matches the selected phone region.
    Returns (passes: bool, reason: str).
    """
    if phone_region == "all":
        return True, "all regions accepted"

    accepted = _REGION_CODES.get(phone_region, [])
    for code in accepted:
        if number.startswith(code):
            return True, f"matches {phone_region} ({code})"

    return False, f"does not match {phone_region} region"


# ─── Mobile validation per region (spec §11.7) ───────────────────────────

def _is_mobile(number: str, source_field: str = "Mobile") -> tuple:
    """Check if a normalised number is a valid mobile by regional rules.
    Returns (is_mobile: bool, reason: str).
    """
    digits_after_plus = number[1:] if number.startswith("+") else number

    # UK +44
    if number.startswith("+44"):
        local = digits_after_plus[2:]  # After "44"
        if local.startswith("7") and len(local) == 10:
            return True, "UK: local starts with 7, length 10"
        return False, f"UK: local '{local[:3]}...' doesn't match mobile pattern (7xxx, 10 digits)"

    # France +33
    if number.startswith("+33"):
        local = digits_after_plus[2:]
        if local and local[0] in ("6", "7"):
            return True, "France: local starts with 6 or 7"
        return False, "France: not a mobile prefix"

    # Germany +49
    if number.startswith("+49"):
        local = digits_after_plus[2:]
        if local[:2] in ("15", "16", "17"):
            return True, f"Germany: local starts with {local[:2]}"
        return False, "Germany: not a mobile prefix (15/16/17)"

    # Spain +34
    if number.startswith("+34"):
        local = digits_after_plus[2:]
        if local and local[0] in ("6", "7"):
            return True, "Spain: local starts with 6 or 7"
        return False, "Spain: not a mobile prefix"

    # Italy +39
    if number.startswith("+39"):
        local = digits_after_plus[2:]
        if local and local[0] == "3":
            return True, "Italy: local starts with 3"
        return False, "Italy: not a mobile prefix"

    # Netherlands +31
    if number.startswith("+31"):
        local = digits_after_plus[2:]
        if local and local[0] == "6":
            return True, "Netherlands: local starts with 6"
        return False, "Netherlands: not a mobile prefix"

    # Belgium +32
    if number.startswith("+32"):
        local = digits_after_plus[2:]
        if local and local[0] == "4":
            return True, "Belgium: local starts with 4"
        return False, "Belgium: not a mobile prefix"

    # Sweden +46
    if number.startswith("+46"):
        local = digits_after_plus[2:]
        if local and local[0] == "7":
            return True, "Sweden: local starts with 7"
        return False, "Sweden: not a mobile prefix"

    # Norway +47
    if number.startswith("+47"):
        local = digits_after_plus[2:]
        if local and local[0] in ("4", "9"):
            return True, "Norway: local starts with 4 or 9"
        return False, "Norway: not a mobile prefix"

    # Switzerland +41
    if number.startswith("+41"):
        local = digits_after_plus[2:]
        if local and local[0] == "7":
            return True, "Switzerland: local starts with 7"
        return False, "Switzerland: not a mobile prefix"

    # Austria +43
    if number.startswith("+43"):
        local = digits_after_plus[2:]
        if local and local[0] == "6":
            return True, "Austria: local starts with 6"
        return False, "Austria: not a mobile prefix"

    # Denmark +45
    if number.startswith("+45"):
        local = digits_after_plus[2:]
        if local and local[0] in ("2", "3", "4", "5", "6"):
            return True, f"Denmark: local starts with {local[0]}"
        return False, "Denmark: not a mobile prefix"

    # Ireland +353
    if number.startswith("+353"):
        local = digits_after_plus[3:]
        if local and local[0] == "8":
            return True, "Ireland: local starts with 8"
        return False, "Ireland: not a mobile prefix"

    # Portugal +351
    if number.startswith("+351"):
        local = digits_after_plus[3:]
        if local and local[0] == "9":
            return True, "Portugal: local starts with 9"
        return False, "Portugal: not a mobile prefix"

    # Finland +358
    if number.startswith("+358"):
        local = digits_after_plus[3:]
        if local and local[0] in ("4", "5"):
            return True, f"Finland: local starts with {local[0]}"
        return False, "Finland: not a mobile prefix"

    # Luxembourg +352
    if number.startswith("+352"):
        local = digits_after_plus[3:]
        if local and local[0] == "6":
            return True, "Luxembourg: local starts with 6"
        return False, "Luxembourg: not a mobile prefix"

    # Greece +30
    if number.startswith("+30"):
        local = digits_after_plus[2:]
        if local[:2] == "69":
            return True, "Greece: local starts with 69"
        return False, "Greece: not a mobile prefix"

    # US/Canada +1
    if number.startswith("+1"):
        if source_field.lower() == "mobile":
            return True, "US/Canada: sourced from Mobile field"
        return True, "US/Canada: from Phone field (lower confidence mobile fallback)"

    # Unknown country — accept if from Mobile field
    if source_field.lower() == "mobile":
        return True, f"unknown country code, but sourced from Mobile field"

    return False, "unknown country code, not from Mobile field"


# ─── Full phone validation pipeline (spec §11) ───────────────────────────

def validate_phone(raw: str, phone_region: str, mode: str,
                   source_field: str = "Mobile", log: bool = True) -> tuple:
    """Run the complete phone validation pipeline.

    Args:
        raw: Raw phone string from the page
        phone_region: Selected phone region ("uk", "us", "europe", "all")
        mode: Collection mode ("mobile", "email", "full", "all")
        source_field: Which field this came from ("Mobile", "Phone", "DDI")
        log: Whether to print the decision log

    Returns:
        (normalised: str, accepted: bool, label: str)
        label is "[Mobile]", "[Phone]", "[DDI]", "[fallback]", or ""
    """
    # Normalise
    normalised = normalise_phone(raw)

    if not normalised:
        if log and raw and raw.strip():
            print(f"  Raw {source_field:<8s}: {raw}")
            print(f"  Normalized  : (empty after normalisation)")
            print(f"  Result      : REJECTED")
        return "", False, ""

    # Junk check
    is_jnk, junk_reason = _is_junk(normalised)
    if is_jnk:
        if log:
            print(f"  Raw {source_field:<8s}: {raw}")
            print(f"  Normalized  : {normalised}")
            print(f"  Junk check  : FAIL — {junk_reason}")
            print(f"  Result      : REJECTED")
        return normalised, False, ""

    # Region check
    region_pass, region_reason = _check_region(normalised, phone_region)
    if not region_pass:
        if log:
            print(f"  Raw {source_field:<8s}: {raw}")
            print(f"  Normalized  : {normalised}")
            print(f"  Region check: FAIL — {region_reason}")
            print(f"  Result      : REJECTED")
        return normalised, False, ""

    # UK range check
    if normalised.startswith("+44"):
        local = normalised[3:]
        range_fail, range_reason = _uk_range_check(local)
        if range_fail:
            if log:
                print(f"  Raw {source_field:<8s}: {raw}")
                print(f"  Normalized  : {normalised}")
                print(f"  Region check: PASS ({phone_region})")
                print(f"  Range check : FAIL — {range_reason}")
                print(f"  Result      : REJECTED")
            return normalised, False, ""

    # US junk check
    if normalised.startswith("+1"):
        local = normalised[2:]
        us_fail, us_reason = _us_junk_check(local)
        if us_fail:
            if log:
                print(f"  Raw {source_field:<8s}: {raw}")
                print(f"  Normalized  : {normalised}")
                print(f"  Region check: PASS ({phone_region})")
                print(f"  US check    : FAIL — {us_reason}")
                print(f"  Result      : REJECTED")
            return normalised, False, ""

    # Mobile validation (only for mobile/full modes)
    requires_mobile = mode in ("mobile", "full")
    if requires_mobile:
        mob_pass, mob_reason = _is_mobile(normalised, source_field)
        if not mob_pass:
            if log:
                print(f"  Raw {source_field:<8s}: {raw}")
                print(f"  Normalized  : {normalised}")
                print(f"  Region check: PASS ({phone_region})")
                print(f"  Range check : PASS")
                print(f"  Mobile check: FAIL — {mob_reason}")
                print(f"  Result      : REJECTED")
            return normalised, False, ""

        label = f"[{source_field}]"
        if log:
            print(f"  Raw {source_field:<8s}: {raw}")
            print(f"  Normalized  : {normalised}")
            print(f"  Region check: PASS ({phone_region})")
            print(f"  Range check : PASS")
            print(f"  Mobile check: PASS ({mob_reason})")
            print(f"  Source      : {source_field} field")
            print(f"  Result      : ACCEPTED {label}")
        return normalised, True, label

    # Non-mobile modes — accept if structurally valid
    label = f"[{source_field}]"
    if log:
        print(f"  Raw {source_field:<8s}: {raw}")
        print(f"  Normalized  : {normalised}")
        print(f"  Region check: PASS ({phone_region})")
        print(f"  Range check : PASS")
        print(f"  Source      : {source_field} field")
        print(f"  Result      : ACCEPTED {label}")
    return normalised, True, label


def resolve_best_phone(fields: dict, phone_region: str, mode: str) -> tuple:
    """Pick the best phone number from mobile, phone, and DDI fields (spec §11.8).

    Returns (best_phone, phone_label, ddi_number)
    """
    # Extract raw values
    raw_mobile = (fields.get("mobile") or fields.get("mobile phone") or "").strip()
    raw_phone = (fields.get("phone") or fields.get("business phone")
                 or fields.get("work phone") or "").strip()
    raw_ddi = (fields.get("ddi") or fields.get("direct dial")
               or fields.get("direct dial in") or fields.get("did") or "").strip()

    best_phone = ""
    best_label = ""

    # Try mobile first
    if raw_mobile:
        norm, accepted, label = validate_phone(
            raw_mobile, phone_region, mode, source_field="Mobile"
        )
        if accepted:
            best_phone = norm
            best_label = label

    # Try phone field if mobile didn't work
    if not best_phone and raw_phone:
        norm, accepted, label = validate_phone(
            raw_phone, phone_region, mode, source_field="Phone"
        )
        if accepted:
            best_phone = norm
            best_label = label

    # If ALL SCRAPE mode and nothing passed, store best normalised as fallback
    if not best_phone and mode == "all":
        for raw, src in [(raw_mobile, "Mobile"), (raw_phone, "Phone")]:
            if raw:
                norm = normalise_phone(raw)
                if norm:
                    best_phone = norm
                    best_label = "[fallback]"
                    print(f"  Result      : STORED AS FALLBACK {best_label}")
                    break

    # DDI is always processed separately
    ddi = ""
    if raw_ddi:
        norm, accepted, _ = validate_phone(
            raw_ddi, phone_region, mode="all", source_field="DDI", log=True
        )
        if accepted:
            ddi = norm

    return best_phone, best_label, ddi
