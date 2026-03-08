from __future__ import annotations

import re
from typing import Dict, List

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

DATE_LINE_RE = re.compile(
    r"^(?:Today|Yesterday|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})$",
    re.IGNORECASE,
)

TIMESTAMP_RE = re.compile(
    r"^(?:Today|Yesterday|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})"
    r",\s*\d{1,2}:\d{2}$",
    re.IGNORECASE,
)

PHONE_RE = re.compile(r"\+?\d[\d\s\-\(\)]{8,}")

JUNK_LINES = {
    "sms",
    "phone",
    "zoom phone",
    "zoom",
    "message",
    "reply",
    "send",
    "file",
    "text",
    "type a message",
    "new message",
    "search",
}


def normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("+"):
        d = re.sub(r"\D+", "", s)
        return ("+" + d) if len(d) >= 7 else ""
    d = re.sub(r"\D+", "", s)
    if len(d) == 10:
        return "+1" + d
    if len(d) == 11 and d[0] == "1":
        return "+" + d
    if len(d) >= 7:
        return "+" + d
    return ""


def extract_name_from_our_message(text: str) -> str:
    """
    Extract first name from opening greeting like:
    Hey Kelly,
    Hi James,
    Hello Sarah
    Dear David
    """
    m = re.match(
        r"^\s*(?:Hey|Hi|Hello|Dear)\s+"
        r"(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?)?\s*"
        r"([A-Z][a-z]{1,25})",
        (text or "").strip(),
        re.IGNORECASE,
    )
    return m.group(1).capitalize() if m else ""


def classify_reply(text: str) -> str:
    t = (text or "").lower()

    stop_words = [
        "stop", "unsubscribe", "opt out", "remove me", "do not text",
        "dont text", "don't text", "no more", "wrong number"
    ]
    not_interested = [
        "not interested", "no thanks", "no thank you", "pass", "decline",
        "not for me", "not relevant"
    ]
    meeting = [
        "call", "meeting", "schedule", "available", "free",
        "let's chat", "lets chat", "let's talk", "lets talk"
    ]
    interested = [
        "interested", "sounds good", "tell me more", "yes", "sure",
        "send me", "would love", "happy to"
    ]

    if any(x in t for x in stop_words):
        return "STOP"
    if any(x in t for x in not_interested):
        return "NOT_INTERESTED"
    if any(x in t for x in meeting):
        return "MEETING"
    if any(x in t for x in interested):
        return "INTERESTED"
    return "UNKNOWN"


def clean_lines(raw_text: str) -> List[str]:
    out = []
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.lower() in JUNK_LINES:
            continue
        out.append(s)
    return out


def looks_like_sender_line(line: str) -> bool:
    if not line:
        return False
    if line == "You":
        return True
    return bool(normalize_phone(line))


def extract_phone(raw_text: str) -> str:
    lines = clean_lines(raw_text)

    for line in lines[:30]:
        if line == "You":
            continue

        # prefer lines that are basically just a phone number
        stripped = line.strip()
        if re.fullmatch(r"\+?\d[\d\s\-\(\)]{7,}", stripped):
            phone = normalize_phone(stripped)
            if phone and 7 <= len(re.sub(r"\D+", "", phone)) <= 15:
                return phone

    for line in lines[:30]:
        if line == "You":
            continue

        m = PHONE_RE.search(line)
        if m:
            candidate = m.group(0)
            phone = normalize_phone(candidate)
            digits = re.sub(r"\D+", "", phone)
            if phone and 7 <= len(digits) <= 15:
                return phone

    return ""


def parse_message_blocks(raw_text: str) -> List[Dict]:
    """
    Parse Zoom thread into message blocks using:
    - date line (e.g. Mar 5 / Today / Yesterday)
    - sender line (You or a phone number)
    - timestamp line (e.g. Mar 5, 13:49)
    - message body

    Returns a list of dicts like:
    {
        "sender": "You" or "+14048035110",
        "sender_phone": "",
        "direction": "outbound" or "inbound",
        "timestamp": "Mar 5, 13:49",
        "date_section": "Mar 5",
        "content": "message body..."
    }
    """
    lines = clean_lines(raw_text)
    messages: List[Dict] = []

    current_date = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Date section marker, e.g. "Mar 5" or "Today"
        if DATE_LINE_RE.match(line):
            current_date = line
            i += 1
            continue

        # Sender line
        if looks_like_sender_line(line):
            sender_line = line
            direction = "outbound" if sender_line == "You" else "inbound"
            sender_phone = "" if sender_line == "You" else normalize_phone(sender_line)

            timestamp = ""
            body_lines: List[str] = []

            j = i + 1

            # Immediate next line may be the timestamp
            if j < len(lines) and TIMESTAMP_RE.match(lines[j]):
                timestamp = lines[j]
                j += 1

            # Collect body until the next structural boundary
            while j < len(lines):
                nxt = lines[j]

                if DATE_LINE_RE.match(nxt):
                    break
                if looks_like_sender_line(nxt):
                    break
                if TIMESTAMP_RE.match(nxt) and body_lines:
                    break

                body_lines.append(nxt)
                j += 1

            content = " ".join(body_lines).strip()

            if content:
                messages.append({
                    "sender": sender_line,
                    "sender_phone": sender_phone,
                    "direction": direction,
                    "timestamp": timestamp,
                    "date_section": current_date,
                    "content": content,
                })

            i = j
            continue

        i += 1

    return messages


def parse_zoom_thread(raw_text: str) -> Dict:
    """
    Main parser entrypoint.

    Returns:
    {
        "phone": "+14048035110",
        "contact_name": "Kelly",
        "messages": [...],
        "our_message": "...",
        "our_message_time": "Mar 5, 13:49",
        "their_response": "Where is it?",
        "their_response_time": "Mar 5, 21:34",
        "classification": "UNKNOWN",
        "last_reply": "Where is it?",
        "confidence": 0.85,
        "valid": True
    }
    """
    phone = extract_phone(raw_text)
    messages = parse_message_blocks(raw_text)

    our_messages = [m for m in messages if m["direction"] == "outbound"]
    their_messages = [m for m in messages if m["direction"] == "inbound"]

    our_message = our_messages[0]["content"] if our_messages else ""
    our_message_time = our_messages[0]["timestamp"] if our_messages else ""

    their_response = their_messages[-1]["content"] if their_messages else ""
    their_response_time = their_messages[-1]["timestamp"] if their_messages else ""

    contact_name = extract_name_from_our_message(our_message) if our_message else ""
    classification = classify_reply(their_response) if their_response else "NO_REPLY"

    confidence = 0.0
    if phone:
        confidence += 0.35
    if messages:
        confidence += 0.30
    if our_message:
        confidence += 0.15
    if their_response:
        confidence += 0.15
    if contact_name:
        confidence += 0.05

    confidence = min(confidence, 1.0)
    valid = bool(phone and messages and confidence >= 0.50)

    return {
        "phone": phone,
        "contact_name": contact_name,
        "messages": messages,
        "our_message": our_message,
        "our_message_time": our_message_time,
        "their_response": their_response,
        "their_response_time": their_response_time,
        "classification": classification,
        "last_reply": their_response,
        "confidence": confidence,
        "valid": valid,
    }
