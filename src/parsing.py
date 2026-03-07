"""
src/parsing.py — Contact page parsing (spec §10).

Two extraction methods:
  1. Structured label-to-value DOM extraction via JavaScript (primary)
  2. Line-based fallback

Also handles name extraction (§10.3) and email extraction (§10.4).
"""

import re

# ─── JavaScript for structured field extraction (spec §10.2) ──────────────

EXTRACT_FIELDS_JS = """
function extractFields(root, depth) {
    if (depth > 14) return {};
    var result = {};

    // dt/dd pattern
    var dts = root.querySelectorAll('dt');
    for (var i = 0; i < dts.length; i++) {
        var label = (dts[i].innerText || dts[i].textContent || '').trim();
        var dd = dts[i].nextElementSibling;
        if (dd && dd.tagName === 'DD') {
            var value = (dd.innerText || dd.textContent || '').trim();
            if (label && value) result[label.toLowerCase()] = value;
        }
    }

    // Class-based label/value pairs
    var labels = root.querySelectorAll('[class*="label"], [class*="Label"]');
    for (var i = 0; i < labels.length; i++) {
        var label = (labels[i].innerText || labels[i].textContent || '').trim();
        var parent = labels[i].parentElement;
        if (parent) {
            var valueEl = parent.querySelector('[class*="value"], [class*="Value"]');
            if (valueEl) {
                var value = (valueEl.innerText || valueEl.textContent || '').trim();
                if (label && value) result[label.toLowerCase()] = value;
            }
        }
    }

    // Recurse into shadow roots
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
        var sr = all[i].shadowRoot;
        if (sr) {
            var sub = extractFields(sr, depth + 1);
            for (var k in sub) if (!result[k]) result[k] = sub[k];
        }
    }
    return result;
}
return extractFields(document, 0);
"""

# ─── Name extraction helpers (spec §10.3) ────────────────────────────────

_TITLE_PREFIXES = re.compile(
    r'^(?:Mr|Mrs|Ms|Miss|Dr|Prof|Sir|Madam|Mx)\.?\s+', re.IGNORECASE
)

_FIELD_LABELS = {
    "phone", "email", "mobile", "title", "account", "name",
    "fax", "address", "description", "record type", "owner",
    "created by", "last modified", "contact record type",
    "edit", "delete", "clone", "save", "cancel",
}

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')


def _clean_name(raw: str) -> tuple:
    """Clean and split a name string into (first, last).

    Returns ("", "") if the string doesn't look like a name.
    """
    if not raw or len(raw) < 4 or len(raw) > 70:
        return "", ""

    # Skip known UI strings / field labels
    if raw.lower().strip() in _FIELD_LABELS:
        return "", ""

    # Strip title prefixes
    cleaned = _TITLE_PREFIXES.sub('', raw).strip()
    if not cleaned:
        return "", ""

    parts = cleaned.split(None, 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    return first, last


def extract_structured_fields(driver) -> dict:
    """Run the structured label-to-value DOM extraction.

    Returns a dict of {lowercase_label: value_string}.
    """
    try:
        fields = driver.execute_script(EXTRACT_FIELDS_JS)
        if isinstance(fields, dict) and fields:
            return fields
    except Exception as e:
        print(f"  [PARSER] Structured extraction error: {e}")

    return {}


def extract_line_based(driver) -> dict:
    """Fallback: extract page text and parse field values by label proximity.

    Returns a dict of {lowercase_label: value_string}.
    """
    try:
        text = driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        )
    except Exception:
        return {}

    if not text:
        return {}

    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    fields = {}

    target_labels = [
        "name", "account name", "title", "mobile", "phone",
        "email", "secondary email", "secondary e-mail", "personal email",
        "contact record type", "record type", "ddi", "direct dial",
        "job function vertical", "relevant summits", "industry",
        "sub industry",
    ]

    junk = {"edit", "delete", "clone", "save", "cancel", "--", "—", "-"}

    for i, line in enumerate(lines):
        line_lower = line.lower().rstrip(":")
        if line_lower in [t for t in target_labels]:
            # Look at the next non-junk line
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if candidate.lower() in junk or not candidate:
                    continue
                if candidate.lower() in [t for t in target_labels]:
                    break  # Hit another label
                fields[line_lower] = candidate
                break

    return fields


def parse_contact(driver) -> dict:
    """Parse the current contact page. Returns a normalised contact dict."""

    # Method 1 — Structured extraction
    fields = extract_structured_fields(driver)

    # Method 2 — Line-based fallback
    if len(fields) < 3:
        print("  [PARSER] Structured extraction sparse — using line-based fallback")
        fallback = extract_line_based(driver)
        for k, v in fallback.items():
            if k not in fields:
                fields[k] = v

    # Extract name
    raw_name = (fields.get("name") or fields.get("full name") or "").strip()
    account = (fields.get("account name") or fields.get("account") or "").strip()
    title_ = (fields.get("title") or fields.get("job title") or "").strip()

    # Ensure name isn't actually the account name or title
    if raw_name and raw_name.lower() == account.lower():
        raw_name = ""
    if raw_name and raw_name.lower() == title_.lower():
        raw_name = ""

    first_name, last_name = _clean_name(raw_name)

    # Extract emails
    primary_email = (fields.get("email") or "").strip()
    secondary_email = (
        fields.get("secondary email") or
        fields.get("secondary e-mail") or
        fields.get("personal email") or ""
    ).strip()

    # Fallback: regex scan for emails in full page text
    if not primary_email:
        try:
            text = driver.execute_script(
                "return document.body ? document.body.innerText : '';"
            )
            found = _EMAIL_RE.findall(text)
            if found:
                primary_email = found[0]
                if len(found) > 1 and found[1] != primary_email:
                    secondary_email = secondary_email or found[1]
        except Exception:
            pass

    # Extract phone fields (raw — normalisation happens in phone.py)
    raw_mobile = (
        fields.get("mobile") or
        fields.get("mobile phone") or ""
    ).strip()
    raw_phone = (
        fields.get("phone") or
        fields.get("business phone") or
        fields.get("work phone") or ""
    ).strip()
    raw_ddi = (
        fields.get("ddi") or
        fields.get("direct dial") or
        fields.get("direct dial in") or
        fields.get("did") or ""
    ).strip()

    # Extract record type fields (for sponsor/delegate checks)
    contact_record_type = (fields.get("contact record type") or "").strip()
    record_type = (fields.get("record type") or "").strip()

    # Warnings
    warnings_raw = (fields.get("warnings") or fields.get("warning") or "").strip()

    # Additional fields
    job_function = (fields.get("job function vertical") or "").strip()
    relevant_summits = (fields.get("relevant summits") or "").strip()
    industry = (fields.get("industry") or "").strip()
    sub_industry = (fields.get("sub industry") or "").strip()

    return {
        "first_name": first_name,
        "last_name": last_name,
        "company": account,
        "title": title_,
        "email": primary_email,
        "secondary_email": secondary_email,
        "mobile": raw_mobile,
        "phone": raw_phone,
        "ddi": raw_ddi,
        "contact_record_type": contact_record_type,
        "record_type": record_type,
        "warnings_raw": warnings_raw,
        "job_function": job_function,
        "relevant_summits": relevant_summits,
        "industry": industry,
        "sub_industry": sub_industry,
    }
