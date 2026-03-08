"""
outlook/outlook_reader.py — Read and classify incoming Outlook emails.

Uses win32com.client to talk directly to the running Outlook desktop app.
No API keys, no OAuth — just COM automation.

USAGE:
    from outlook.outlook_reader import OutlookReader

    reader = OutlookReader()
    emails = reader.read_inbox(days=7)
    for e in emails:
        print(e["from"], e["subject"], e["classification"])
"""

import re
from datetime import datetime, timedelta

import json
from pathlib import Path

try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


CHECKPOINT_PATH = Path("outlook_reader_checkpoint.json")


def load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        return {"processed_entry_ids": []}
    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_entry_ids": []}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ─── Classification keywords ────────────────────────────────────────────────

INTERESTED_KEYWORDS = [
    "interested", "yes", "would love to", "count me in", "attend", "register",
    "confirm", "accept", "delighted to", "gladly", "rsvp yes", "look forward to",
    "sounds great", "sounds good", "happy to join", "i can attend",
    "i'd be interested", "i would be interested", "please register me",
    "please sign me up", "this sounds interesting", "i am interested", "i’m interested",
]

MORE_INFO_KEYWORDS = [
    "more info", "more information", "details", "agenda", "schedule", "cost",
    "pricing", "dates", "location", "speaker list", "send more", "brochure",
    "itinerary", "who else", "can you share more", "can you send", "please send",
    "please share", "what is the agenda", "what are the dates", "where is it",
    "who will be there", "what summit is this", "tell me more",
]

FOLLOW_UP_KEYWORDS = [
    "out of office", "ooo", "vacation", "travelling", "traveling",
    "maternity leave", "get back to you", "follow up", "check my schedule",
    "tentative", "next week", "forwarded to", "passing this along",
    "discussing with", "revert", "circle back", "speak next week",
    "not the right time", "reach back out", "please follow up", "try me again",
    "i am away", "currently away",
]

UNDELIVERABLE_KEYWORDS = [
    "undeliverable", "delivery failed", "bounced", "user unknown", "not found",
    "no longer with", "left the company", "retired", "message rejected",
    "mailer-daemon", "postmaster", "recipient address rejected", "550 ", "554 ",
    "mailbox unavailable", "delivery status notification", "failure notice",
    "returned mail",
]

NOT_INTERESTED_KEYWORDS = [
    "not interested", "no thank you", "no thanks", "decline", "cannot attend",
    "can't make it", "unable to", "busy", "schedule conflict", "unfortunately",
    "pass", "regrets", "unsubscribe", "remove me", "take me off", "please remove",
    "please unsubscribe", "do not contact", "not relevant", "wrong person",
    "i am not interested", "i’m not interested",
]

MISC_KEYWORDS = [
    "received", "acknowledged", "thank you for reaching out", "hello", "noted",
]


def classify_reply(subject: str, body: str, sender_email: str = "") -> str:
    """
    Classify an email reply into one of:
      - INTERESTED_REPLY
      - MORE_INFO
      - FOLLOW_UP
      - UNDELIVERABLE
      - NOT_INTERESTED
      - MISC
    """
    text = f"{subject or ''}\n{body or ''}\n{sender_email or ''}".lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in text for k in keywords)

    if has_any(UNDELIVERABLE_KEYWORDS):
        return "UNDELIVERABLE"

    if has_any(NOT_INTERESTED_KEYWORDS):
        return "NOT_INTERESTED"

    if has_any(INTERESTED_KEYWORDS):
        return "INTERESTED_REPLY"

    if has_any(MORE_INFO_KEYWORDS):
        return "MORE_INFO"

    if has_any(FOLLOW_UP_KEYWORDS):
        return "FOLLOW_UP"

    if has_any(MISC_KEYWORDS):
        return "MISC"

    return "MISC"


def _normalize_phone(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    digits = re.sub(r"\D+", "", raw)

    if raw.startswith("+"):
        return f"+{digits}" if 7 <= len(digits) <= 15 else ""

    # US/Canada
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"

    # UK local mobile-ish
    if len(digits) == 11 and digits.startswith("07"):
        return f"+44{digits[1:]}"

    # generic fallback
    if 7 <= len(digits) <= 15:
        return f"+{digits}"

    return ""


def _extract_phone(text: str) -> str:
    """
    Extract a likely phone number from an email body/signature.
    Tries stricter patterns first, then generic fallback.
    """
    if not text:
        return ""

    patterns = [
        r"\+\d[\d\-\s\(\)]{7,}\d",
        r"\b07\d{9}\b",
        r"\b1?\d{10}\b",
        r"\(\d{3}\)\s*\d{3}[-\s]?\d{4}",
        r"\b\d{3}[-\s]\d{3}[-\s]\d{4}\b",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, text):
            phone = _normalize_phone(match)
            if phone:
                return phone

    return ""


def shape_email(item) -> dict:
    """
    Convert a COM Outlook item into a normalized dict for classification/logging.
    """
    subject = getattr(item, "Subject", "") or ""
    sender_name = getattr(item, "SenderName", "") or ""
    sender_email = ""
    try:
        sender_email = getattr(item, "SenderEmailAddress", "") or ""
    except Exception:
        sender_email = ""

    body = getattr(item, "Body", "") or ""
    received_time = ""
    try:
        received_time = str(getattr(item, "ReceivedTime", "") or "")
    except Exception:
        received_time = ""

    entry_id = ""
    try:
        entry_id = getattr(item, "EntryID", "") or ""
    except Exception:
        entry_id = ""

    conversation_topic = ""
    try:
        conversation_topic = getattr(item, "ConversationTopic", "") or ""
    except Exception:
        conversation_topic = ""

    phone = _extract_phone(body)
    classification = classify_reply(subject, body, sender_email)

    return {
        "entry_id": entry_id,
        "subject": subject,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "body": body,
        "received_time": received_time,
        "conversation_topic": conversation_topic,
        "phone": phone,
        "classification": classification,
    }


# ─── OutlookReader ───────────────────────────────────────────────────────────

class OutlookReader:
    """Read emails from the local Outlook desktop app."""

    def __init__(self):
        if not HAS_WIN32:
            raise RuntimeError(
                "win32com not available. Install: pip install pywin32\n"
                "Outlook desktop app must be running."
            )
        self._outlook = None

    def _connect(self):
        if self._outlook is None:
            self._outlook = win32com.client.Dispatch(
                "Outlook.Application"
            ).GetNamespace("MAPI")
        return self._outlook

    def read_inbox(self, days: int = 7, max_emails: int = 500,
                   folder_name: str = None) -> list:
        """
        Read recent emails from inbox (or a named subfolder).

        Returns list of dicts:
            {from_name, from_email, subject, body, received,
             classification, phone, has_attachment, message_id}
        """
        ns = self._connect()

        if folder_name:
            inbox = ns.GetDefaultFolder(6)  # 6 = Inbox
            folder = None
            for f in inbox.Folders:
                if f.Name.lower() == folder_name.lower():
                    folder = f
                    break
            if not folder:
                print(f"  Folder '{folder_name}' not found. Using Inbox.")
                folder = inbox
        else:
            folder = ns.GetDefaultFolder(6)

        items = folder.Items
        items.Sort("[ReceivedTime]", True)  # newest first

        cutoff = (datetime.now() - timedelta(days=days)).replace(tzinfo=None)
        results = []

        for i, item in enumerate(items):
            if i >= max_emails:
                break

            try:
                body = getattr(item, "Body", "") or ""
                subject = getattr(item, "Subject", "") or ""
                if not subject and not body:
                    continue

                email_data = shape_email(item)
                
                try:
                    received = item.ReceivedTime
                    received_naive = (
                        received.replace(tzinfo=None)
                        if hasattr(received, "tzinfo")
                        else received
                    )
                    if received_naive < cutoff:
                        break
                    email_data["received_time"] = received_naive.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

                results.append(email_data)
            except Exception:
                continue

        return results

    def read_sent(self, days: int = 30, max_emails: int = 500) -> list:
        """Read sent emails — useful for matching replies to outbound."""
        ns = self._connect()
        sent = ns.GetDefaultFolder(5)  # 5 = Sent Items
        items = sent.Items
        items.Sort("[SentOn]", True)

        cutoff = (datetime.now() - timedelta(days=days)).replace(tzinfo=None)
        results = []

        for i, item in enumerate(items):
            if i >= max_emails:
                break
            try:
                sent_time = item.SentOn
                sent_naive = (
                    sent_time.replace(tzinfo=None)
                    if hasattr(sent_time, "tzinfo")
                    else sent_time
                )
                if sent_naive < cutoff:
                    break
            except Exception:
                continue

            try:
                to_list = []
                try:
                    for recip in item.Recipients:
                        to_list.append(recip.Address)
                except Exception:
                    pass

                results.append({
                    "to":       "; ".join(to_list),
                    "subject":  (getattr(item, "Subject", "") or "").strip(),
                    "body":     (getattr(item, "Body", "") or "")[:2000],
                    "sent":     sent_naive.strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception:
                continue

        return results

    def get_folders(self) -> list:
        """List all inbox subfolders."""
        ns = self._connect()
        inbox = ns.GetDefaultFolder(6)
        folders = ["Inbox"]
        for f in inbox.Folders:
            folders.append(f.Name)
        return folders


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    print()
    print("=" * 56)
    print("  OUTLOOK EMAIL READER")
    print("=" * 56)

    reader = OutlookReader()

    if len(sys.argv) > 1 and sys.argv[1] == "--auto":
        import time
        
        try:
            from runtime import write_tool_state, read_tool_state, touch_heartbeat
            HAS_RUNTIME = True
        except ImportError:
            HAS_RUNTIME = False
            
        try:
            from core.database import log_outreach
            HAS_DB = True
        except ImportError:
            HAS_DB = False

        if HAS_RUNTIME:
            write_tool_state("outlook_reader", status="running")
            
        checkpoint = load_checkpoint()
        processed_entry_ids = set(checkpoint.get("processed_entry_ids", []))

        print("\n  [AUTO] Starting continuous inbox monitoring...")
        print("  Checking for new replies every 5 minutes.\n")
        
        try:
            while True:
                if HAS_RUNTIME:
                    state = read_tool_state("outlook_reader")
                    if state.get("status") == "stopped_requested":
                        break
                        
                emails = reader.read_inbox(days=1, max_emails=50)
                
                saved_count = 0
                if HAS_DB:
                    for email_data in emails:
                        entry_id = email_data.get("entry_id", "")
                        if entry_id and entry_id in processed_entry_ids:
                            continue

                        # Prevent logging empty emails
                        if not email_data.get("sender_email") and not email_data.get("subject"):
                            continue
                            
                        try:
                            log_outreach(
                                channel="email",
                                direction="inbound",
                                message=email_data.get("body", "")[:2000],
                                contact_name=email_data.get("sender_name", ""),
                                phone=email_data.get("phone", ""),
                                classification=email_data.get("classification", "MISC"),
                                timestamp=email_data.get("received_time", ""),
                            )
                            saved_count += 1
                            if entry_id:
                                processed_entry_ids.add(entry_id)
                        except Exception as log_err:
                            pass
                
                checkpoint["processed_entry_ids"] = list(processed_entry_ids)[-5000:]
                save_checkpoint(checkpoint)
                
                if HAS_RUNTIME:
                    touch_heartbeat("outlook_reader")

                print(f"  [{datetime.now().time().strftime('%H:%M:%S')}] Checked inbox. Found {len(emails)} recent. Inserted {saved_count} new email(s).")
                
                # Check bounds before sleep if stop requested
                # Sleep in increments so it can stop faster
                for _ in range(300):
                    if HAS_RUNTIME:
                        st = read_tool_state("outlook_reader")
                        if st.get("status") == "stopped_requested":
                            break
                    time.sleep(1)
                    
                if HAS_RUNTIME:
                    st = read_tool_state("outlook_reader")
                    if st.get("status") == "stopped_requested":
                        break
                        
        except Exception as e:
            if HAS_RUNTIME:
                write_tool_state("outlook_reader", status="failed", error=str(e))
            print(f"  [ERROR] Outlook Reader crashed: {e}")
            return
            
        if HAS_RUNTIME:
            write_tool_state("outlook_reader", status="stopped")
        print("  [AUTO] Gracefully stopped.")
        return

    # Show available folders
    folders = reader.get_folders()
    print(f"\n  Available folders: {', '.join(folders)}")

    # Read recent emails
    days = 7
    raw = input(f"\n  How many days back? (default {days}): ").strip()
    if raw.isdigit():
        days = int(raw)

    print(f"\n  Reading inbox (last {days} days)...\n")
    emails = reader.read_inbox(days=days)

    if not emails:
        print("  No emails found.\n")
        return

    # Summary by classification
    from collections import Counter
    counts = Counter(e["classification"] for e in emails)
    print(f"  Found {len(emails)} emails:")
    for cls, cnt in sorted(counts.items()):
        print(f"    {cls:20s} — {cnt}")
    print()

    # Show interesting ones
    interesting = [e for e in emails if e["classification"] not in ("UNKNOWN",)]
    if interesting:
        print(f"  Classified emails ({len(interesting)}):")
        print(f"  {'─' * 52}")
        for e in interesting[:30]:
            subj = e["subject"][:45] + ("..." if len(e["subject"]) > 45 else "")
            print(f"  [{e['classification']:17s}] {e['from_name'][:20]:20s} | {subj}")
        if len(interesting) > 30:
            print(f"  ... and {len(interesting) - 30} more")
    print()


if __name__ == "__main__":
    main()
