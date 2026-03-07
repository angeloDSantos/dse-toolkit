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

try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# ─── Classification keywords ────────────────────────────────────────────────

_CLASSIFICATIONS = {
    "stop": {
        "keywords": ["stop", "unsubscribe", "opt out", "opt-out",
                      "do not text", "dont text", "do not contact",
                      "remove me", "take me off", "no more"],
        "label": "STOP",
    },
    "not_interested": {
        "keywords": ["not interested", "no thank you", "no thanks",
                      "pass on this", "not for me", "decline",
                      "not relevant", "wrong person"],
        "label": "NOT_INTERESTED",
    },
    "interested": {
        "keywords": ["interested", "tell me more", "sounds good",
                      "love to", "would like", "happy to",
                      "count me in", "sign me up", "let's do it",
                      "yes please", "i'm in", "im in"],
        "label": "INTERESTED",
    },
    "more_info": {
        "keywords": ["more info", "more information", "more details",
                      "send me", "can you share", "what does",
                      "how does", "agenda", "brochure", "when is",
                      "where is", "what time"],
        "label": "MORE_INFO",
    },
    "meeting": {
        "keywords": ["book a call", "schedule a call", "set up a meeting",
                      "calendar invite", "available", "free on",
                      "let's chat", "let's talk", "jump on a call",
                      "30 minutes", "15 minutes"],
        "label": "MEETING_REQUEST",
    },
}


def _classify_text(text: str) -> str:
    """Classify email body text. Returns classification label."""
    t = (text or "").lower()
    # Check STOP first (highest priority)
    for cat_key in ["stop", "not_interested", "meeting", "interested", "more_info"]:
        cat = _CLASSIFICATIONS[cat_key]
        if any(kw in t for kw in cat["keywords"]):
            return cat["label"]
    return "UNKNOWN"


def _extract_phone(text: str) -> str:
    """Try to extract a phone number from email text."""
    if not text:
        return ""
    m = re.search(r"\+1\D*(\d{3})\D*(\d{3})\D*(\d{4})", text)
    if m:
        return "+1" + "".join(m.groups())
    m = re.search(r"\+44\D*(\d{4})\D*(\d{6})", text)
    if m:
        return "+44" + "".join(m.groups())
    m = re.search(r"(\d{3})\D*(\d{3})\D*(\d{4})", text)
    if m:
        return "+1" + "".join(m.groups())
    return ""


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
                received = item.ReceivedTime
                received_naive = (
                    received.replace(tzinfo=None)
                    if hasattr(received, "tzinfo")
                    else received
                )
                if received_naive < cutoff:
                    break
            except Exception:
                continue

            try:
                subject = getattr(item, "Subject", "") or ""
                body    = getattr(item, "Body", "") or ""
                sender  = ""
                sender_email = ""

                try:
                    sender = getattr(item, "SenderName", "") or ""
                    sender_email = getattr(item, "SenderEmailAddress", "") or ""
                except Exception:
                    pass

                full_text = f"{subject}\n{body}"
                classification = _classify_text(full_text)
                phone = _extract_phone(full_text)

                has_attachment = False
                try:
                    has_attachment = item.Attachments.Count > 0
                except Exception:
                    pass

                message_id = ""
                try:
                    message_id = getattr(item, "EntryID", "") or ""
                except Exception:
                    pass

                results.append({
                    "from_name":      sender,
                    "from_email":     sender_email,
                    "subject":        subject.strip(),
                    "body":           body[:2000],  # cap for safety
                    "received":       received_naive.strftime("%Y-%m-%d %H:%M:%S"),
                    "classification": classification,
                    "phone":          phone,
                    "has_attachment":  has_attachment,
                    "message_id":     message_id,
                })
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
    print()
    print("=" * 56)
    print("  OUTLOOK EMAIL READER")
    print("=" * 56)

    reader = OutlookReader()

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
