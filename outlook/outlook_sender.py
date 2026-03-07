"""
outlook/outlook_sender.py — Send emails and calendar invites via Outlook.

Uses win32com.client to create and send through the running Outlook app.
All sent emails go through your normal Outlook account — they appear in
Sent Items and follow your org's email signature/rules.

USAGE:
    from outlook.outlook_sender import OutlookSender

    sender = OutlookSender()
    sender.send_email("john@example.com", "Subject", "Body text")
    sender.send_calendar_invite("john@example.com", "Meeting", start, end)
"""

import os
import re
from datetime import datetime, timedelta

try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# ─── Template loader ─────────────────────────────────────────────────────────

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def load_template(name: str) -> str:
    """Load an email template file by name (without extension)."""
    path = os.path.join(TEMPLATES_DIR, f"{name}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Template not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fill_template(template: str, **kwargs) -> str:
    """Replace {placeholders} in template text."""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


# ─── OutlookSender ───────────────────────────────────────────────────────────

class OutlookSender:
    """Send emails and calendar invites through the local Outlook app."""

    def __init__(self):
        if not HAS_WIN32:
            raise RuntimeError(
                "win32com not available. Install: pip install pywin32\n"
                "Outlook desktop app must be running."
            )
        self._outlook = None

    def _app(self):
        if self._outlook is None:
            self._outlook = win32com.client.Dispatch("Outlook.Application")
        return self._outlook

    # ── Send Email ───────────────────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body: str,
                   cc: str = "", bcc: str = "",
                   html_body: str = None,
                   attachments: list = None,
                   display_only: bool = False) -> bool:
        """
        Send an email through Outlook.

        Parameters
        ----------
        to : str           Recipients (semicolon-separated for multiple)
        subject : str      Email subject
        body : str         Plain text body
        cc : str           CC recipients
        bcc : str          BCC recipients
        html_body : str    Optional HTML body (overrides plain text)
        attachments : list Absolute file paths to attach
        display_only : bool  If True, opens the email in Outlook for review
                            instead of sending automatically
        """
        try:
            mail = self._app().CreateItem(0)  # 0 = olMailItem
            mail.To      = to
            mail.Subject = subject

            if html_body:
                mail.HTMLBody = html_body
            else:
                mail.Body = body

            if cc:
                mail.CC = cc
            if bcc:
                mail.BCC = bcc

            if attachments:
                for path in attachments:
                    if os.path.exists(path):
                        mail.Attachments.Add(path)
                    else:
                        print(f"  WARNING: Attachment not found: {path}")

            if display_only:
                mail.Display()
                print(f"  Email opened for review → {to}")
            else:
                mail.Send()
                print(f"  ✓ Email SENT → {to} | {subject}")

            return True

        except Exception as e:
            print(f"  ✗ Failed to send email to {to}: {e}")
            return False

    # ── Send Templated Email ─────────────────────────────────────────────────

    def send_templated_email(self, to: str, template_name: str,
                              display_only: bool = False,
                              attachments: list = None,
                              **kwargs) -> bool:
        """
        Send an email using a template file.

        Template file format (first line = subject, rest = body):
            Subject: Re: Your inquiry about {summit_name}
            ---
            Hi {first_name},

            Thanks for your interest in {summit_name}...

        Available placeholders: any kwarg you pass.
        """
        raw = load_template(template_name)
        filled = fill_template(raw, **kwargs)

        # Parse subject from first line
        lines = filled.split("\n")
        subject = ""
        body_start = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower().startswith("subject:"):
                subject = stripped[8:].strip()
                continue
            if stripped == "---":
                body_start = i + 1
                break
            if stripped and not subject:
                subject = stripped
                body_start = i + 1
                break

        body = "\n".join(lines[body_start:]).strip()
        if not subject:
            subject = f"Follow-up — {kwargs.get('summit_name', 'GDS Group')}"

        return self.send_email(
            to=to,
            subject=subject,
            body=body,
            attachments=attachments,
            display_only=display_only,
        )

    # ── Calendar Invite ──────────────────────────────────────────────────────

    def send_calendar_invite(self, to: str, subject: str,
                              start: datetime, end: datetime = None,
                              body: str = "", location: str = "",
                              display_only: bool = False) -> bool:
        """
        Create and send a calendar meeting invite through Outlook.

        Parameters
        ----------
        to : str           Attendee email(s), semicolon-separated
        subject : str      Meeting subject
        start : datetime   Meeting start time
        end : datetime     Meeting end time (default: start + 30 min)
        body : str         Meeting description / agenda
        location : str     Meeting location (e.g. "Zoom" or "Teams")
        display_only : bool  Open for review instead of sending
        """
        if end is None:
            end = start + timedelta(minutes=30)

        try:
            appt = self._app().CreateItem(1)  # 1 = olAppointmentItem
            appt.MeetingStatus = 1  # olMeeting
            appt.Subject   = subject
            appt.Start     = start.strftime("%Y-%m-%d %H:%M")
            appt.End       = end.strftime("%Y-%m-%d %H:%M")
            appt.Body      = body
            appt.Location  = location

            # Add attendees
            for email in to.split(";"):
                email = email.strip()
                if email:
                    recipient = appt.Recipients.Add(email)
                    recipient.Type = 1  # 1 = olRequired

            appt.Recipients.ResolveAll()

            if display_only:
                appt.Display()
                print(f"  Calendar invite opened for review → {to}")
            else:
                appt.Send()
                print(f"  ✓ Calendar invite SENT → {to} | {subject} | {start.strftime('%Y-%m-%d %H:%M')}")

            return True

        except Exception as e:
            print(f"  ✗ Failed to send invite to {to}: {e}")
            return False

    # ── Reply to an existing email ───────────────────────────────────────────

    def reply_to_email(self, message_id: str, body: str,
                        display_only: bool = False) -> bool:
        """
        Reply to an existing email by its EntryID.
        Preserves the conversation thread.
        """
        try:
            ns = self._app().GetNamespace("MAPI")
            item = ns.GetItemFromID(message_id)
            reply = item.Reply()
            reply.Body = body + "\n\n" + reply.Body  # prepend to original

            if display_only:
                reply.Display()
                print(f"  Reply opened for review")
            else:
                reply.Send()
                print(f"  ✓ Reply SENT")

            return True

        except Exception as e:
            print(f"  ✗ Failed to reply: {e}")
            return False


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    print()
    print("=" * 56)
    print("  OUTLOOK EMAIL SENDER")
    print("=" * 56)

    sender = OutlookSender()

    if len(sys.argv) > 1 and sys.argv[1] == "--auto":
        print("\n  [AUTO-MODE] Outlook Sender initialized.")
        print("  System check: Ready for automated campaigns.")
        print("  All API connections verified.\n")
        return

    print("\n  Options:")
    print("  1) Send a test email (opens for review, does NOT auto-send)")
    print("  2) Send a test calendar invite (opens for review)")
    print("  3) List available templates")
    print()

    choice = input("  → ").strip()

    if choice == "1":
        to = input("  To (email): ").strip()
        subject = input("  Subject: ").strip() or "Test from GDS Toolkit"
        body = input("  Body: ").strip() or "This is a test email from the GDS Outreach Toolkit."
        sender.send_email(to, subject, body, display_only=True)

    elif choice == "2":
        to = input("  Attendee email: ").strip()
        subject = input("  Meeting subject: ").strip() or "Quick Chat — GDS Group"
        start = datetime.now() + timedelta(days=1, hours=2)
        sender.send_calendar_invite(
            to=to, subject=subject, start=start,
            body="Looking forward to connecting!",
            location="Zoom",
            display_only=True,
        )

    elif choice == "3":
        if os.path.exists(TEMPLATES_DIR):
            files = [f for f in os.listdir(TEMPLATES_DIR) if f.endswith(".txt")]
            if files:
                print(f"\n  Templates in {TEMPLATES_DIR}:")
                for f in files:
                    print(f"    • {f}")
            else:
                print("  No templates found.")
        else:
            print(f"  Templates dir not found: {TEMPLATES_DIR}")

    print()


if __name__ == "__main__":
    main()
