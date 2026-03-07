"""
src/csv_io.py — CSV initialisation, saving, and row counting (thread-safe).
"""

import csv
import os
import threading

_csv_lock = threading.Lock()

# ─── Column headers for each mode ───────────────────────────────────────────

H_MOBILE = ["First Name", "Last Name", "Company", "Phone",
            "Warnings", "Scraped At", "Contact URL", "Order URL"]

H_EMAIL  = ["First Name", "Last Name", "Company", "Email",
            "Warnings", "Scraped At", "Contact URL", "Order URL"]

H_FULL   = ["First Name", "Last Name", "Company", "Title",
            "Phone", "Email", "Warnings",
            "Scraped At", "Contact URL", "Order URL"]

H_ALL    = ["First Name", "Last Name", "Company", "Title",
            "Email", "Secondary Email", "Phone",
            "Warnings", "Contact URL", "Order URL", "Scraped At"]

H_DDI    = ["First Name", "Last Name", "Company",
            "DDI Number", "Warnings", "Contact URL", "Order URL"]


def headers_for_mode(mode: str) -> list:
    return {
        "mobile": H_MOBILE,
        "email":  H_EMAIL,
        "full":   H_FULL,
        "all":    H_ALL,
    }[mode]


# ─── Init / Save / Count ────────────────────────────────────────────────────

def init_csv(path: str, headers: list):
    """Create CSV with headers if it doesn't exist. Thread-safe."""
    with _csv_lock:
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)


def save_row(path: str, row: list):
    """Append a single row to CSV. Thread-safe."""
    with _csv_lock:
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)


def count_csv_rows(path: str) -> int:
    """Count data rows (excluding header) in a CSV. Thread-safe."""
    with _csv_lock:
        if not os.path.exists(path):
            return 0
        try:
            with open(path, encoding="utf-8") as f:
                return max(0, sum(1 for _ in f) - 1)
        except Exception:
            return 0
