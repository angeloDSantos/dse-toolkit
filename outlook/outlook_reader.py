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
import sys
import os
from datetime import datetime, timedelta

import json
from pathlib import Path

# Ensure the root project directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


CHECKPOINT_PATH = Path("outlook_reader_checkpoint.json")


def load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        return {"processed_entry_ids": [], "processed_fallback_keys": []}
    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_entry_ids": [], "processed_fallback_keys": []}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def make_fallback_email_key(email_data: dict) -> str:
    """
    Fallback dedupe key in case EntryID is missing or unstable.
    """
    base = "|".join([
        email_data.get("sender_email", "") or "",
        email_data.get("subject", "") or "",
        email_data.get("received_time", "") or "",
    ])
    import hashlib
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


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
    subject = subject or ""
    body = body or ""
    sender_email = sender_email or ""

    text = f"{subject}\n{body}\n{sender_email}".lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in text for k in keywords)

    # Strong bounce heuristics first
    if "mailer-daemon" in sender_email.lower() or "postmaster" in sender_email.lower():
        return "UNDELIVERABLE"

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
    Convert an Outlook COM item into one normalized dict for classification/logging.
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

    def read_inbox(self, start_date: datetime = None, end_date: datetime = None, max_emails: int = 500,
                   folder_name: str = None) -> list:
        """
        Read emails from inbox (or a named subfolder) within a date range.
        Returns list of dicts.
        """
        ns = self._connect()
        inbox = ns.GetDefaultFolder(6)  # 6 = Inbox

        target_folder = inbox
        if folder_name:
            for f in inbox.Folders:
                if f.Name.lower() == folder_name.lower():
                    target_folder = f
                    break
            if target_folder == inbox:
                print(f"  Folder '{folder_name}' not found. Using Inbox.")

        items = target_folder.Items
        items.Sort("[ReceivedTime]", True)  # newest first

        if not start_date:
            start_date = (datetime.now() - timedelta(days=7)).replace(tzinfo=None)
        if not end_date:
            end_date = datetime.now().replace(tzinfo=None)
            
        results = []
        count = 0

        for item in items:
            if count >= max_emails:
                break

            try:
                subject = getattr(item, "Subject", "") or ""
                body = getattr(item, "Body", "") or ""
                if not subject and not body:
                    continue

                received_obj = getattr(item, "ReceivedTime", None)
                if received_obj:
                    try:
                        received_naive = (
                            received_obj.replace(tzinfo=None)
                            if hasattr(received_obj, "tzinfo")
                            else received_obj
                        )
                        if received_naive < start_date:
                            break # We've gone past the start date, stop looking
                        if received_naive > end_date:
                            continue # Skip emails newer than the end date
                    except Exception:
                        pass

                email_data = shape_email(item)
                
                # Re-stamp normalized time
                if received_obj:
                    try:
                        received_naive = (
                            received_obj.replace(tzinfo=None)
                            if hasattr(received_obj, "tzinfo")
                            else received_obj
                        )
                        email_data["received_time"] = received_naive.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                results.append(email_data)
                count += 1
            except Exception as exc:
                print(f"[READ] Skipped one item: {exc}", flush=True)
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


def log_email_to_app(email_data: dict) -> None:
    """
    Log one normalized email into the app database using the existing outreach logger.
    Adjust the imported logger name if needed for your project.
    """
    from core.database import log_outreach

    log_outreach(
        phone=email_data.get("phone", ""),
        contact_name=email_data.get("sender_name", ""),
        channel="email",
        direction="inbound",
        message=email_data.get("body", ""),
        classification=email_data.get("classification", "MISC"),
        timestamp=email_data.get("received_time", ""),
    )


def run_auto_loop(poll_seconds: int = 300, start_date_filter: datetime = None, end_date_filter: datetime = None, max_emails: int = 50, folder_name: str | None = None):
    """
    Continuous inbox polling loop with:
    - EntryID dedupe
    - fallback composite-key dedupe
    - normalized email processing
    - heartbeat
    """
    import time
    try:
        from runtime import write_tool_state, read_tool_state, touch_heartbeat
        HAS_RUNTIME = True
    except ImportError:
        HAS_RUNTIME = False

    if HAS_RUNTIME:
        write_tool_state("outlook_reader", status="running")

    checkpoint = load_checkpoint()
    processed_entry_ids = set(checkpoint.get("processed_entry_ids", []))
    processed_fallback_keys = set(checkpoint.get("processed_fallback_keys", []))

    print("[AUTO] Outlook continuous monitor started", flush=True)

    try:
        reader = OutlookReader()
        while True:
            if HAS_RUNTIME:
                try:
                    state = read_tool_state("outlook_reader")
                    if state.get("status") == "stopped_requested":
                        break
                except Exception:
                    pass

            loop_start = start_date_filter if start_date_filter else (datetime.now() - timedelta(days=1)).replace(tzinfo=None)
            loop_end = end_date_filter if end_date_filter else datetime.now().replace(tzinfo=None)

            emails = reader.read_inbox(start_date=loop_start, end_date=loop_end, max_emails=max_emails, folder_name=folder_name)

            inserted = 0
            duplicate_skipped = 0
            failed = 0

            for email_data in emails:
                entry_id = email_data.get("entry_id", "") or ""
                fallback_key = make_fallback_email_key(email_data)

                already_seen = False
                if entry_id and entry_id in processed_entry_ids:
                    already_seen = True
                elif fallback_key in processed_fallback_keys:
                    already_seen = True

                if already_seen:
                    duplicate_skipped += 1
                    continue

                try:
                    log_email_to_app(email_data)
                    inserted += 1

                    if entry_id:
                        processed_entry_ids.add(entry_id)
                    processed_fallback_keys.add(fallback_key)

                except Exception as exc:
                    failed += 1
                    print(f"[AUTO] Failed to log email '{email_data.get('subject','')[:60]}': {exc}", flush=True)

            checkpoint["processed_entry_ids"] = list(processed_entry_ids)[-5000:]
            checkpoint["processed_fallback_keys"] = list(processed_fallback_keys)[-5000:]
            save_checkpoint(checkpoint)

            if HAS_RUNTIME:
                try:
                    touch_heartbeat("outlook_reader")
                except Exception:
                    pass

            print(
                f"  [{datetime.now().time().strftime('%H:%M:%S')}] Checked inbox | inserted={inserted} | duplicates={duplicate_skipped} | failed={failed}",
                flush=True,
            )

            for _ in range(poll_seconds):
                if HAS_RUNTIME:
                    try:
                        st = read_tool_state("outlook_reader")
                        if st.get("status") == "stopped_requested":
                            break
                    except Exception:
                        pass
                time.sleep(1)
            
            if HAS_RUNTIME:
                st = read_tool_state("outlook_reader")
                if st.get("status") == "stopped_requested":
                    break

    except Exception as exc:
        if HAS_RUNTIME:
            try:
                write_tool_state("outlook_reader", status="failed", error=str(exc))
            except Exception:
                pass
        raise

    if HAS_RUNTIME:
        try:
            write_tool_state("outlook_reader", status="stopped")
        except Exception:
            pass
    print("  [AUTO] Gracefully stopped.", flush=True)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    print()
    print("=" * 56)
    print("  OUTLOOK EMAIL READER")
    print("=" * 56)

    reader = OutlookReader()

    if "--auto" in sys.argv:
        start_date_filter = None
        end_date_filter = None
        
        if "--prompt-config" in sys.argv:
            ans = input("\n  Do you want to reset the checkpoint and re-read old emails? (y/N): ").strip().lower()
            if ans == 'y':
                if CHECKPOINT_PATH.exists():
                    CHECKPOINT_PATH.unlink()
                print("  Checkpoint reset!")
                
            print("\n  Set the date range for emails to read.")
            print("  Format: YYYY-MM-DD (e.g., 2024-01-15)")
            print("  Leave blank to read everything from the last 7 days.\n")
            
            raw_start = input("  Read emails FROM (Start Date): ").strip()
            if raw_start:
                try:
                    start_date_filter = datetime.strptime(raw_start, "%Y-%m-%d")
                    print(f"  Start date set to: {start_date_filter.strftime('%Y-%m-%d')}")
                except ValueError:
                    print("  Invalid format. Using default start date (7 days ago).")
                    
            raw_end = input("  Read emails TO (End Date): ").strip()
            if raw_end:
                try:
                    # Set the end date to the very end of the specified day
                    parsed_end = datetime.strptime(raw_end, "%Y-%m-%d")
                    end_date_filter = parsed_end.replace(hour=23, minute=59, second=59)
                    print(f"  End date set to: {end_date_filter.strftime('%Y-%m-%d 23:59:59')}")
                except ValueError:
                    print("  Invalid format. Using default end date (Now).")

        run_auto_loop(poll_seconds=300, start_date_filter=start_date_filter, end_date_filter=end_date_filter)
        return

    # Show available folders
    folders = reader.get_folders()
    print(f"\n  Available folders: {', '.join(folders)}")

    # Read recent emails
    start_date = None
    end_date = None
    
    print("\n  Format: YYYY-MM-DD (e.g., 2024-01-15)")
    print("  Leave blank for defaults.\n")
    
    raw_start = input("  Read emails FROM (Start Date): ").strip()
    if raw_start:
        try:
            start_date = datetime.strptime(raw_start, "%Y-%m-%d")
        except ValueError:
            print("  Invalid format. Using default.")
            
    raw_end = input("  Read emails TO (End Date): ").strip()
    if raw_end:
        try:
            parsed_end = datetime.strptime(raw_end, "%Y-%m-%d")
            end_date = parsed_end.replace(hour=23, minute=59, second=59)
        except ValueError:
            print("  Invalid format. Using default.")

    if not start_date:
        days = 7
        raw = input(f"\n  How many days back? (default {days}): ").strip()
        if raw.isdigit():
            days = int(raw)
        start_date = (datetime.now() - timedelta(days=days)).replace(tzinfo=None)

    if not end_date:
        end_date = datetime.now().replace(tzinfo=None)

    print(f"\n  Reading inbox from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...\n")
    emails = reader.read_inbox(start_date=start_date, end_date=end_date)

    if not emails:
        print("  No emails found.\n")
        return

    def print_email_summary(emails: list[dict], only_non_misc: bool = False) -> None:
        rows = emails
        if only_non_misc:
            rows = [e for e in rows if e.get("classification") != "MISC"]

        print(f"\nFound {len(rows)} email(s):\n")
        for e in rows:
            subj = (e.get("subject") or "—")[:70]
            sender = (e.get("sender_name") or e.get("sender_email") or "—")[:40]
            cls = e.get("classification") or "MISC"
            phone = e.get("phone") or "—"
            ts = e.get("received_time") or "—"

            print(f"Subject : {subj}")
            print(f"From    : {sender}")
            print(f"Class   : {cls}")
            print(f"Phone   : {phone}")
            print(f"Received: {ts}")
            print("-" * 60)

    print("\nOptions:")
    print("  1) Read recent inbox emails")
    print("  2) Read recent inbox emails (non-MISC only)")
    print()

    choice = input("  -> ").strip()

    if choice == "1":
        print_email_summary(emails, only_non_misc=False)
    elif choice == "2":
        print_email_summary(emails, only_non_misc=True)
    else:
        print("Unknown option.")


if __name__ == "__main__":
    main()
