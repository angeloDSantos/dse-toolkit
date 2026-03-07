"""
zoom_sms/zoom_reader.py — Read and parse Zoom SMS conversations.

Two approaches:
  1. Zoom's local SQLite DB (%APPDATA%/Zoom/data/*/zoomdb.db)
  2. pyautogui screen scraping for live conversation reading

The sender's first name is extracted from the FIRST outbound message
in each conversation (the one YOU sent), since it contains their name
in the template: "Hey {first_name}, ..."

USAGE:
    from zoom_sms.zoom_reader import ZoomReader

    reader = ZoomReader()
    convos = reader.read_conversations()
    for c in convos:
        print(c["phone"], c["contact_name"], c["last_reply"])
"""

import os
import re
import csv
import json
import time
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import pyautogui
    import pyperclip
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


# ─── Config ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVO_DB = os.path.join(DATA_DIR, "conversations.db")
COORDS_FILE = os.path.join(BASE_DIR, "coords.json")

os.makedirs(DATA_DIR, exist_ok=True)


# ─── Conversation database ──────────────────────────────────────────────────

def _init_convo_db():
    con = sqlite3.connect(CONVO_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            phone         TEXT PRIMARY KEY,
            contact_name  TEXT,
            last_message  TEXT,
            direction     TEXT,
            timestamp     TEXT,
            classification TEXT DEFAULT 'UNKNOWN',
            raw_thread    TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            phone     TEXT NOT NULL,
            direction TEXT NOT NULL,
            content   TEXT,
            timestamp TEXT,
            UNIQUE(phone, direction, content)
        )
    """)
    con.commit()
    con.close()


def save_conversation(phone, contact_name, messages, classification="UNKNOWN"):
    """Save a parsed conversation to the local DB."""
    _init_convo_db()
    con = sqlite3.connect(CONVO_DB)
    try:
        last = messages[-1] if messages else {}
        con.execute("""
            INSERT OR REPLACE INTO conversations
            (phone, contact_name, last_message, direction, timestamp, classification, raw_thread)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            phone,
            contact_name,
            last.get("content", ""),
            last.get("direction", ""),
            last.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            classification,
            json.dumps(messages),
        ))
        for msg in messages:
            try:
                con.execute("""
                    INSERT OR IGNORE INTO messages
                    (phone, direction, content, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (phone, msg.get("direction", ""), msg.get("content", ""),
                      msg.get("timestamp", "")))
            except Exception:
                pass
        con.commit()
    finally:
        con.close()


def load_all_conversations() -> list:
    """Load all stored conversations."""
    _init_convo_db()
    con = sqlite3.connect(CONVO_DB)
    try:
        rows = con.execute(
            "SELECT phone, contact_name, last_message, direction, "
            "timestamp, classification FROM conversations "
            "ORDER BY timestamp DESC"
        ).fetchall()
        return [
            {
                "phone": r[0], "contact_name": r[1],
                "last_message": r[2], "direction": r[3],
                "timestamp": r[4], "classification": r[5],
            }
            for r in rows
        ]
    finally:
        con.close()


# ─── Zoom DB scanning ───────────────────────────────────────────────────────

def _find_zoom_db_paths() -> list:
    """Find all Zoom local database files."""
    candidates = []
    appdata = os.environ.get("APPDATA", "")
    zoom_data = os.path.join(appdata, "Zoom", "data")

    if os.path.isdir(zoom_data):
        for entry in os.scandir(zoom_data):
            if entry.is_dir():
                db = os.path.join(entry.path, "zoomdb.db")
                if os.path.isfile(db):
                    candidates.append(db)
        direct = os.path.join(zoom_data, "zoomdb.db")
        if os.path.isfile(direct):
            candidates.append(direct)
    return candidates


def _scan_zoom_db(db_path: str) -> list:
    """
    Read messages from a Zoom SQLite database.
    Returns list of {phone, content, direction, timestamp}.
    """
    results = []
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(db_path, tmp.name)
    except Exception:
        return results

    try:
        con = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        sms_tables = [t for t in tables
                      if any(k in t.lower()
                             for k in ("sms", "message", "chat", "im"))]

        for table in sms_tables:
            try:
                cols = [r[1] for r in
                        con.execute(f"PRAGMA table_info({table})").fetchall()]
                cols_lower = [c.lower() for c in cols]

                # Find relevant columns
                number_col = next(
                    (c for c in cols_lower
                     if any(k in c for k in
                            ("number", "phone", "recipient", "peer", "to", "dst"))),
                    None,
                )
                content_col = next(
                    (c for c in cols_lower
                     if any(k in c for k in
                            ("content", "body", "message", "text", "msg"))),
                    None,
                )
                dir_col = next(
                    (c for c in cols_lower
                     if any(k in c for k in
                            ("direction", "type", "send", "outgoing", "is_send"))),
                    None,
                )
                time_col = next(
                    (c for c in cols_lower
                     if any(k in c for k in
                            ("time", "date", "timestamp", "created"))),
                    None,
                )

                if not number_col or not content_col:
                    continue

                actual_cols = {
                    "number": cols[cols_lower.index(number_col)],
                    "content": cols[cols_lower.index(content_col)],
                }
                if dir_col:
                    actual_cols["direction"] = cols[cols_lower.index(dir_col)]
                if time_col:
                    actual_cols["time"] = cols[cols_lower.index(time_col)]

                select = ", ".join(actual_cols.values())
                rows = con.execute(
                    f"SELECT {select} FROM {table} ORDER BY rowid DESC LIMIT 5000"
                ).fetchall()

                for row in rows:
                    phone_raw = str(row[0] or "")
                    content = str(row[1] or "")
                    direction = ""
                    timestamp = ""

                    if "direction" in actual_cols:
                        d = row[list(actual_cols.keys()).index("direction")]
                        if str(d) in ("1", "send", "outgoing", "sent"):
                            direction = "outbound"
                        else:
                            direction = "inbound"

                    if "time" in actual_cols:
                        t = row[list(actual_cols.keys()).index("time")]
                        timestamp = str(t or "")

                    phone = _normalize_phone(phone_raw)
                    if phone and content:
                        results.append({
                            "phone": phone,
                            "content": content,
                            "direction": direction,
                            "timestamp": timestamp,
                        })

            except Exception:
                continue

        con.close()
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return results


# ─── Extract sender name from outbound template ─────────────────────────────

def _extract_name_from_template(outbound_text: str) -> str:
    """
    Extract the contact's first name from the outbound SMS template.
    Templates start with "Hey {first_name}," or "Hi {first_name},"
    """
    if not outbound_text:
        return ""
    m = re.match(
        r"(?:Hey|Hi|Hello|Dear)\s+([A-Z][a-z]+)",
        outbound_text.strip(),
        re.IGNORECASE,
    )
    return m.group(1) if m else ""


# ─── Classify reply ──────────────────────────────────────────────────────────

_REPLY_CLASSES = {
    "STOP": ["stop", "unsubscribe", "opt out", "remove me", "do not text",
             "dont text", "don't text", "no more", "wrong number"],
    "INTERESTED": ["interested", "sounds good", "tell me more", "love to",
                   "yes", "sure", "sign me up", "count me in", "send me",
                   "i'm in", "im in"],
    "NOT_INTERESTED": ["not interested", "no thanks", "no thank you",
                       "pass", "decline", "not for me", "wrong person"],
    "MEETING": ["call", "meeting", "schedule", "available", "free",
                "let's chat", "let's talk"],
}


def classify_reply(text: str) -> str:
    t = (text or "").lower()
    for cls in ["STOP", "NOT_INTERESTED", "MEETING", "INTERESTED"]:
        if any(kw in t for kw in _REPLY_CLASSES[cls]):
            return cls
    return "UNKNOWN"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("+"):
        digits = re.sub(r"\D+", "", s)
        if len(digits) >= 7:
            return "+" + digits
    digits = re.sub(r"\D+", "", s)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) >= 7:
        return "+" + digits
    return ""


# ─── ZoomReader ──────────────────────────────────────────────────────────────

class ZoomReader:
    """Read Zoom SMS conversations from local DB and/or screen."""

    def __init__(self):
        _init_convo_db()

    def scan_zoom_database(self) -> dict:
        """
        Scan Zoom's local SQLite DB for all SMS messages.
        Groups by phone number, extracts contact names from outbound templates.

        Returns dict: {phone: {contact_name, messages: [...], classification}}
        """
        db_paths = _find_zoom_db_paths()
        if not db_paths:
            print("  No Zoom database found at %APPDATA%/Zoom/data/")
            return {}

        print(f"  Found {len(db_paths)} Zoom DB file(s)")
        all_messages = []
        for dbp in db_paths:
            msgs = _scan_zoom_db(dbp)
            all_messages.extend(msgs)
            print(f"    {dbp} → {len(msgs)} messages")

        # Group by phone
        by_phone = defaultdict(list)
        for msg in all_messages:
            by_phone[msg["phone"]].append(msg)

        conversations = {}
        for phone, msgs in by_phone.items():
            # Sort by timestamp if available
            msgs.sort(key=lambda m: m.get("timestamp", ""))

            # Extract contact name from first outbound message
            contact_name = ""
            for msg in msgs:
                if msg.get("direction") == "outbound":
                    contact_name = _extract_name_from_template(msg["content"])
                    if contact_name:
                        break

            # Classify based on last inbound message
            last_inbound = ""
            for msg in reversed(msgs):
                if msg.get("direction") == "inbound":
                    last_inbound = msg["content"]
                    break

            classification = classify_reply(last_inbound) if last_inbound else "NO_REPLY"

            conversations[phone] = {
                "contact_name": contact_name,
                "messages": msgs,
                "classification": classification,
                "last_reply": last_inbound,
                "message_count": len(msgs),
            }

            # Save to local DB
            save_conversation(phone, contact_name, msgs, classification)

        return conversations

    def read_screen_conversation(self) -> dict:
        """
        Read the currently visible Zoom SMS conversation from screen.
        Uses Ctrl+A/Ctrl+C to grab the conversation text.

        Returns {phone, contact_name, messages, classification}
        """
        if not HAS_PYAUTOGUI:
            print("  pyautogui not available. Install: pip install pyautogui pyperclip")
            return {}

        print("  Reading visible Zoom conversation...")
        print("  Make sure Zoom Phone SMS tab is visible and a conversation is open.")
        input("  Press ENTER when ready...")

        # Copy conversation text
        pyperclip.copy("")
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.5)
        pyautogui.press("escape")
        time.sleep(0.2)

        raw = pyperclip.paste()
        if not raw or len(raw) < 20:
            print("  Clipboard empty — couldn't read conversation.")
            return {}

        return self._parse_conversation_text(raw)

    def _parse_conversation_text(self, raw: str) -> dict:
        """Parse raw Ctrl+A text from a Zoom SMS conversation."""
        lines = [l.strip() for l in raw.splitlines() if l.strip()]

        # Try to find the phone number (usually at top of conversation)
        phone = ""
        for line in lines[:10]:
            m = re.search(r"\+?\d[\d\s\-()]{8,}", line)
            if m:
                phone = _normalize_phone(m.group(0))
                if phone:
                    break

        # Parse messages — look for timestamp patterns
        # Zoom typically shows: "Today 2:35 PM" or "Mar 5 10:15 AM"
        messages = []
        current_content = []
        current_direction = ""

        time_re = re.compile(
            r"^(?:Today|Yesterday|"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})"
            r"\s+\d{1,2}:\d{2}\s*(?:AM|PM)?",
            re.IGNORECASE,
        )

        for line in lines:
            if time_re.match(line):
                # Save previous message
                if current_content:
                    text = " ".join(current_content)
                    messages.append({
                        "content": text,
                        "direction": current_direction,
                        "timestamp": "",
                    })
                current_content = []
                current_direction = ""
                continue

            if not line or line in ("SMS", "Phone", "Zoom Phone"):
                continue

            current_content.append(line)

        # Save last message
        if current_content:
            text = " ".join(current_content)
            messages.append({
                "content": text,
                "direction": "",
                "timestamp": "",
            })

        # Try to determine direction and extract contact name
        # The first long message is likely our outbound template
        contact_name = ""
        for msg in messages:
            text = msg["content"]
            name = _extract_name_from_template(text)
            if name:
                msg["direction"] = "outbound"
                contact_name = name
                break

        # Any message after an outbound that doesn't look like our template
        # is likely inbound
        found_outbound = False
        for msg in messages:
            if msg["direction"] == "outbound":
                found_outbound = True
                continue
            if found_outbound and not msg["direction"]:
                # Short messages are likely replies (inbound)
                if len(msg["content"]) < 200:
                    msg["direction"] = "inbound"
                else:
                    msg["direction"] = "outbound"

        # Classify
        last_reply = ""
        for msg in reversed(messages):
            if msg.get("direction") == "inbound":
                last_reply = msg["content"]
                break

        classification = classify_reply(last_reply) if last_reply else "NO_REPLY"

        result = {
            "phone": phone,
            "contact_name": contact_name,
            "messages": messages,
            "classification": classification,
            "last_reply": last_reply,
        }

        # Save
        if phone:
            save_conversation(phone, contact_name, messages, classification)

        return result

    def get_stored_conversations(self) -> list:
        """Get all previously stored conversations."""
        return load_all_conversations()

    def get_replies_only(self) -> list:
        """Get only conversations that have inbound replies."""
        all_convos = load_all_conversations()
        return [c for c in all_convos
                if c.get("direction") == "inbound" and c.get("last_message")]


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 56)
    print("  ZOOM SMS READER")
    print("=" * 56)

    reader = ZoomReader()

    print("\n  Options:")
    print("  1) Scan Zoom database for messages")
    print("  2) Read current on-screen conversation")
    print("  3) View stored conversations")
    print("  4) View replies only")
    print()

    choice = input("  → ").strip()

    if choice == "1":
        print("\n  Scanning Zoom local database...")
        convos = reader.scan_zoom_database()
        if not convos:
            print("  No conversations found.\n")
            return

        print(f"\n  Found {len(convos)} conversations:\n")
        print(f"  {'Phone':<16} {'Name':<15} {'Msgs':>4} {'Class':<17} {'Last Reply'}")
        print(f"  {'─'*16} {'─'*15} {'─'*4} {'─'*17} {'─'*30}")

        for phone, data in sorted(convos.items(),
                                    key=lambda x: x[1].get("classification", "")):
            name = data["contact_name"] or "—"
            cnt  = data["message_count"]
            cls  = data["classification"]
            last = (data.get("last_reply") or "—")[:40]
            print(f"  {phone:<16} {name:<15} {cnt:>4} {cls:<17} {last}")

        # Summary
        from collections import Counter
        counts = Counter(d["classification"] for d in convos.values())
        print(f"\n  Summary:")
        for cls, cnt in sorted(counts.items()):
            print(f"    {cls:<17} — {cnt}")

    elif choice == "2":
        result = reader.read_screen_conversation()
        if result:
            print(f"\n  Phone:    {result.get('phone', '—')}")
            print(f"  Contact:  {result.get('contact_name', '—')}")
            print(f"  Class:    {result.get('classification', '—')}")
            print(f"  Messages: {len(result.get('messages', []))}")
            if result.get("last_reply"):
                print(f"  Last reply: {result['last_reply'][:100]}")
        else:
            print("  Could not read conversation.")

    elif choice == "3":
        convos = reader.get_stored_conversations()
        if not convos:
            print("  No stored conversations.\n")
            return
        print(f"\n  {len(convos)} stored conversations:\n")
        for c in convos[:30]:
            name = c.get("contact_name") or "—"
            last = (c.get("last_message") or "—")[:50]
            print(f"  {c['phone']:<16} {name:<15} [{c['classification']}] {last}")

    elif choice == "4":
        replies = reader.get_replies_only()
        if not replies:
            print("  No reply conversations found.\n")
            return
        print(f"\n  {len(replies)} conversations with replies:\n")
        for c in replies:
            name = c.get("contact_name") or "—"
            last = (c.get("last_message") or "—")[:50]
            print(f"  {c['phone']:<16} {name:<15} [{c['classification']}] {last}")

    print()


if __name__ == "__main__":
    main()
