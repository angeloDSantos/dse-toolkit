"""
src/parsing.py — Contact page text extraction and field parsing.
"""

import re
from src.phone import normalise_phone, validate_phone


# ─── Warning patterns ───────────────────────────────────────────────────────

_WARN_PATTERNS = [
    re.compile(r"\bopen\s+opp\b",                              re.I),
    re.compile(r"\bopen\s+opportunity\b",                      re.I),
    re.compile(r"\bdnc\b",                                     re.I),
    re.compile(r"\bdo\s+not\s+contact\b",                      re.I),
    re.compile(r"\bdo\s+not\s+email\b",                        re.I),
    re.compile(r"\bcontact\s+(has\s+been|is)\s+blacklisted\b", re.I),
    re.compile(r"\bthis\s+contact\s+is\s+blacklisted\b",       re.I),
    re.compile(r"\bimportant\b.*\bblacklisted\b",              re.I),
    re.compile(r"\byellow\s*card\b",                           re.I),
]

# ─── UI junk lines to skip ──────────────────────────────────────────────────

_UI_JUNK = [
    "skip to navigation", "skip to main content", "search...",
    "favorites list", "global actions", "guidance center", "salesforce help",
    "view profile", "app launcher", "delegates lighting",
    "show more navigation items", "edit nav items",
]

_NAME_TITLES  = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir|madam|mx)\.?\s+", re.I)
_EMAIL_RE     = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_FIELD_LABEL_RE = re.compile(
    r"^(phone|mobile|email|name|title|account|company|address|industry|"
    r"ddi|direct dial|record type|contact record type|warnings|"
    r"related|details|activity|news|chatter|"
    r"delegates lighting|delegates|lightning experience|"
    r"home|files|dashboards|reports|campaigns|leads|"
    r"accounts|contacts|opportunities|cases|forecasts|feeds)$",
    re.I,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _clean_lines(text):
    return [re.sub(r"\s+", " ", l.strip()) for l in (text or "").splitlines() if l.strip()]


def _header(lines):
    try:
        return lines[:lines.index("Tabs")]
    except ValueError:
        return lines[:160]


def _is_junk_line(line):
    ll = (line or "").strip().lower()
    return not ll or ll.startswith("skip to") or any(p in ll for p in _UI_JUNK)


def _name_like(s):
    if not s or len(s) < 4 or len(s) > 70:
        return False
    parts = s.split()
    if len(parts) < 2 or len(parts) > 6:
        return False
    return all(re.match(r"^[A-Za-z\u00C0-\u024F'\-]+$", p) for p in parts)


def _after(lines, *labels):
    for i, line in enumerate(lines):
        if (line or "").strip() in labels:
            for nxt in lines[i + 1:i + 6]:
                nxt = (nxt or "").strip()
                if nxt and not _is_junk_line(nxt):
                    return nxt
    return ""


def _split_name(full):
    cleaned = _NAME_TITLES.sub("", (full or "").strip()).strip()
    parts   = cleaned.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _all_emails(text: str) -> list:
    seen, out = set(), []
    for m in _EMAIL_RE.finditer(text or ""):
        e = m.group(0).strip().lower()
        if e not in seen:
            seen.add(e)
            out.append(m.group(0).strip())
    return out


# ─── Interruption / ready detection ─────────────────────────────────────────

def looks_like_interruption(text: str) -> bool:
    t = (text or "").lower()
    return (
        "sorry to interrupt" in t
        or ("css error" in t and "refresh" in t)
        or t.strip() == "lightning experience"
    )


def contact_page_ready(text: str) -> bool:
    t = (text or "").lower()
    if looks_like_interruption(text):
        return False
    signals = ("mobile", "phone", "email", "account name", "title", "ddi", "direct dial")
    return any(s in t for s in signals) and len(t) > 250


# ─── Main parser ─────────────────────────────────────────────────────────────

def parse_contact(text: str, region: str) -> dict:
    """
    Parse raw page text from a Salesforce Contact page into a structured dict.
    """
    lines  = _clean_lines(text)
    header = _header(lines)
    name   = ""
    skip_vals = {
        "Account Name", "Title", "Phone", "Mobile", "Email", "DDI",
        "Direct Dial", "Direct Dial In", "Secondary Email", "Personal Email",
    }

    try:
        idx = header.index("Name")
        for c in header[idx + 1:idx + 18]:
            c = _NAME_TITLES.sub("", (c or "").strip()).strip()
            if c and not _is_junk_line(c) and c not in skip_vals and _name_like(c):
                name = c
                break
    except ValueError:
        pass
    if not name:
        for c in header[:110]:
            c = _NAME_TITLES.sub("", (c or "").strip()).strip()
            if c and not _is_junk_line(c) and _name_like(c):
                name = c
                break

    first, last = _split_name(name)

    # Warnings
    warns, wseen = [], set()
    for line in header:
        if not line or _is_junk_line(line) or len(line) > 220:
            continue
        if "blacklisted reason" in line.lower():
            continue
        if any(p.search(line) for p in _WARN_PATTERNS) and line not in wseen:
            wseen.add(line)
            warns.append(line)

    # Emails
    all_emails      = _all_emails(text)
    raw_secondary   = _after(lines, "Secondary Email", "Secondary E-mail")
    email_primary   = all_emails[0] if all_emails else ""
    email_secondary = ""
    if raw_secondary and _EMAIL_RE.match(raw_secondary):
        email_secondary = raw_secondary
    elif len(all_emails) >= 2:
        email_secondary = all_emails[1]

    # Phone fields
    raw_mobile = _after(lines, "Mobile", "Mobile Phone", "Cell", "Cell Phone")
    raw_phone  = _after(lines, "Phone", "Business Phone", "Work Phone")
    raw_ddi    = _after(lines, "DDI", "Direct Dial", "Direct Dial In", "DID")

    mob_n, mob_ok, mob_note = "", False, "no Mobile field"
    if raw_mobile:
        mob_n, mob_ok, mob_note = validate_phone(raw_mobile, True,  region)
    ph_n,  ph_ok,  ph_note  = "", False, "no Phone field"
    if raw_phone:
        ph_n,  ph_ok,  ph_note  = validate_phone(raw_phone,  False, region)

    if mob_ok:
        best, best_note = mob_n, f"[Mobile] {mob_note}"
    elif ph_ok:
        best, best_note = ph_n,  f"[Phone]  {ph_note}"
    else:
        best, best_note = "",    mob_note if raw_mobile else ph_note

    if not best:
        for raw, fm in ((raw_mobile, True), (raw_phone, False)):
            n = normalise_phone(raw)
            if n:
                best = n
                best_note = "[raw-normalised fallback]"
                break

    ddi_n, ddi_ok, ddi_note = "", False, "no DDI field"
    if raw_ddi:
        ddi_n, ddi_ok, ddi_note = validate_phone(raw_ddi, False, region, for_ddi=True)

    return {
        "first_name":           first,
        "last_name":            last,
        "account":              _after(lines, "Account Name", "Account", "Company", "Organisation"),
        "title":                _after(lines, "Title", "Job Title", "Position"),
        "phone":                best,
        "phone_note":           best_note,
        "email":                email_primary,
        "secondary_email":      email_secondary,
        "warnings":             " | ".join(warns),
        "ddi":                  ddi_n if ddi_ok else "",
        "ddi_ok":               ddi_ok,
        "ddi_note":             ddi_note,
        "raw_mobile":           raw_mobile,
        "raw_phone":            raw_phone,
        "raw_ddi":              raw_ddi,
        "contact_record_type":  _after(lines, "Contact Record Type"),
        "record_type":          _after(lines, "Record Type", "Record type (HS-SF)", "Record type"),
        "job_function_vertical": _after(lines, "Job Function Vertical", "Job Function", "Function"),
        "relevant_summits":     _after(lines, "Relevant Summits", "Summits", "Relevant Summit(s)"),
        "industry":             _after(lines, "Industry"),
        "sub_industry":         _after(lines, "Sub Industry", "Sub-Industry", "Sub Industry "),
    }
