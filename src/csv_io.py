"""
src/csv_io.py — CSV output for scraped contacts (spec §12).

Handles per-mode column sets, DDI secondary file, and file naming.
"""

import csv
import os
from datetime import datetime


# ─── Column definitions per mode (spec §12.1) ──────────────────────────────

COLUMNS = {
    "all": [
        "First Name", "Last Name", "Company", "Title",
        "Email", "Secondary Email", "Phone",
        "Warnings", "Contact URL", "Order URL", "Scraped At",
    ],
    "mobile": [
        "First Name", "Last Name", "Company", "Phone",
        "Warnings", "Scraped At", "Contact URL", "Order URL",
    ],
    "email": [
        "First Name", "Last Name", "Company", "Email",
        "Warnings", "Scraped At", "Contact URL", "Order URL",
    ],
    "full": [
        "First Name", "Last Name", "Company", "Title",
        "Phone", "Email",
        "Warnings", "Scraped At", "Contact URL", "Order URL",
    ],
}

DDI_COLUMNS = [
    "First Name", "Last Name", "Company", "DDI Number",
    "Warnings", "Contact URL", "Order URL",
]


def _safe_filename(name: str) -> str:
    """Convert a record name to a filesystem-safe filename."""
    import re
    return re.sub(r'[^\w\-]', '_', name).strip('_')[:80]


class CSVWriter:
    """Manages main and DDI CSV files for a single record/event.

    Usage:
        writer = CSVWriter(output_folder, record_name, mode)
        writer.write_contact(contact_dict)
        writer.write_ddi(contact_dict)
        writer.close()
    """

    def __init__(self, output_folder: str, record_name: str, mode: str):
        self.output_folder = output_folder
        self.record_name = record_name
        self.mode = mode
        self.stem = _safe_filename(record_name)
        os.makedirs(output_folder, exist_ok=True)

        # Main CSV
        self.main_path = os.path.join(output_folder, f"{self.stem}.csv")
        self.main_file = open(self.main_path, "a", newline="", encoding="utf-8")
        self.main_writer = csv.DictWriter(
            self.main_file,
            fieldnames=COLUMNS.get(mode, COLUMNS["full"]),
            extrasaction="ignore",
        )
        if self.main_file.tell() == 0:
            self.main_writer.writeheader()

        # DDI CSV
        self.ddi_path = os.path.join(output_folder, f"{self.stem}_DDI.csv")
        self.ddi_file = open(self.ddi_path, "a", newline="", encoding="utf-8")
        self.ddi_writer = csv.DictWriter(
            self.ddi_file,
            fieldnames=DDI_COLUMNS,
            extrasaction="ignore",
        )
        if self.ddi_file.tell() == 0:
            self.ddi_writer.writeheader()

        self.contact_count = 0
        self.ddi_count = 0

    def write_contact(self, contact: dict, phone: str, contact_url: str,
                      order_url: str, warnings_text: str = ""):
        """Write a contact row to the main CSV."""
        row = {
            "First Name": contact.get("first_name", ""),
            "Last Name": contact.get("last_name", ""),
            "Company": contact.get("company", ""),
            "Title": contact.get("title", ""),
            "Email": contact.get("email", ""),
            "Secondary Email": contact.get("secondary_email", ""),
            "Phone": phone,
            "Warnings": warnings_text,
            "Contact URL": contact_url,
            "Order URL": order_url,
            "Scraped At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.main_writer.writerow(row)
        self.main_file.flush()
        self.contact_count += 1

    def write_ddi(self, contact: dict, ddi_number: str,
                  contact_url: str, order_url: str, warnings_text: str = ""):
        """Write a DDI row to the secondary CSV."""
        if not ddi_number:
            return
        row = {
            "First Name": contact.get("first_name", ""),
            "Last Name": contact.get("last_name", ""),
            "Company": contact.get("company", ""),
            "DDI Number": ddi_number,
            "Warnings": warnings_text,
            "Contact URL": contact_url,
            "Order URL": order_url,
        }
        self.ddi_writer.writerow(row)
        self.ddi_file.flush()
        self.ddi_count += 1

    def close(self):
        """Flush and close file handles."""
        try:
            self.main_file.flush()
            self.main_file.close()
        except Exception:
            pass
        try:
            self.ddi_file.flush()
            self.ddi_file.close()
        except Exception:
            pass

        # Remove empty DDI files
        try:
            if self.ddi_count == 0 and os.path.exists(self.ddi_path):
                os.remove(self.ddi_path)
        except Exception:
            pass
