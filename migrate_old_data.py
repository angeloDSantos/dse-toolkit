"""
migrate_old_data.py — One-shot script to migrate old Zoom Automation data
into the new DSE database, then clean up.

Run once: python migrate_old_data.py
"""

import os
import sys
import csv
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import init_db, get_db, add_contact, log_outreach

OLD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_old_zoom_automation")


def migrate_sms_log():
    """Import sms_log.csv into outreach_log table."""
    path = os.path.join(OLD_DIR, "sms_log.csv")
    if not os.path.exists(path):
        print("  No sms_log.csv found, skipping")
        return 0

    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = (row.get("timestamp") or "").strip()
            fn = (row.get("first_name") or "").strip()
            co = (row.get("company") or "").strip()
            ph = (row.get("phone") or "").strip()
            status = (row.get("status") or "sent").strip().lower()
            note = (row.get("note") or "").strip()

            log_outreach(
                channel="zoom_sms",
                direction="outbound",
                content=note or f"SMS sent to {fn}",
                contact_name=fn,
                contact_phone=ph,
                status=status,
            )
            count += 1

    print(f"  ✓ Imported {count} SMS log entries")
    return count


def migrate_old_contacts():
    """Import olddcontacts.csv into contacts table."""
    path = os.path.join(OLD_DIR, "olddcontacts.csv")
    if not os.path.exists(path):
        print("  No olddcontacts.csv found, skipping")
        return 0

    db = get_db()
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = (row.get("first_name") or "").strip()
            email = (row.get("email") or "").strip()
            if not fn:
                continue

            # Dedup check
            if email:
                dup = db.execute(
                    "SELECT id FROM contacts WHERE email = ? AND email != ''",
                    (email,)
                ).fetchone()
                if dup:
                    continue

            add_contact(
                first_name=fn,
                last_name="",
                company="",
                email=email,
                source="old_zoom_contacts",
            )
            count += 1

    print(f"  ✓ Imported {count} old contacts")
    return count


def migrate_crm_export():
    """Import crm_export.csv into contacts table."""
    path = os.path.join(OLD_DIR, "crm_export.csv")
    if not os.path.exists(path):
        print("  No crm_export.csv found, skipping")
        return 0

    db = get_db()
    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            parts = name.split(" ", 1)
            fn = parts[0]
            ln = parts[1] if len(parts) > 1 else ""
            email = (row.get("Email") or "").strip()
            phone = (row.get("Phone") or "").strip()
            company = (row.get("Account Name") or "").strip()
            title = (row.get("Title") or "").strip()

            # Dedup
            if email:
                dup = db.execute(
                    "SELECT id FROM contacts WHERE email = ? AND email != ''",
                    (email,)
                ).fetchone()
                if dup:
                    continue

            add_contact(
                first_name=fn, last_name=ln, company=company,
                title=title, email=email, phone=phone,
                source="crm_export",
            )
            count += 1

    print(f"  ✓ Imported {count} CRM export contacts")
    return count


def migrate_dnc_list():
    """Import do_not_text.csv phones as STOP entries in outreach_log."""
    path = os.path.join(OLD_DIR, "do_not_text.csv")
    if not os.path.exists(path):
        print("  No do_not_text.csv found, skipping")
        return 0

    count = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ph = (row.get("phone") or "").strip()
            if not ph:
                continue
            log_outreach(
                channel="zoom_sms",
                direction="inbound",
                content="DNC — do not text (migrated from old list)",
                contact_phone=ph,
                status="received",
                classification="STOP",
            )
            count += 1

    print(f"  ✓ Imported {count} DNC entries as STOP classifications")
    return count


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  MIGRATING OLD ZOOM AUTOMATION DATA")
    print("=" * 50)
    print()

    if not os.path.exists(OLD_DIR):
        print(f"  Old data folder not found: {OLD_DIR}")
        sys.exit(1)

    init_db()

    total = 0
    total += migrate_sms_log()
    total += migrate_old_contacts()
    total += migrate_crm_export()
    total += migrate_dnc_list()

    print()
    print(f"  Total records migrated: {total}")
    print()

    # Ask before deleting
    ans = input("  Delete old _old_zoom_automation folder? (y/n): ").strip().lower()
    if ans == "y":
        shutil.rmtree(OLD_DIR)
        print("  ✓ Old folder deleted")
    else:
        print("  Kept old folder")

    print()
    print("  Done!")
