# DSE Toolkit: Comprehensive System Architecture & Feature Guide

## 1. Executive Summary
The DSE Toolkit is a centralized orchestration layer for high-volume outreach and relationship management. It combines independent automation scripts (scrapers and readers) with a persistent local database and a Flask-based command dashboard ("Imperium").

---

## 2. The Launcher Process & Runtime Logic

### A. How Features are Launched
The web dashboard provides a GUI for initiating specialized Python automation scripts located in the root directory (e.g., `scraper.py`, `zoom_sms_reader.py`, `outlook_reader.py`).

1.  **Request:** User clicks a "Launch" button in the Dashboard.
2.  **Subprocess Execution:** The `app.py` backend uses Python's `subprocess` or `os.system` logic to trigger the script.
3.  **Terminal Isolation:** Scripts are often launched in a new terminal window (using `osa_script` or similar on macOS) to allow for interactive CLI prompts (like "Reset config?" or "Enter keywords").

### B. Interactive Script Logic
When a script starts, it typically follows this flow:
- **Config Check:** Queries if the user wants to reset or update temporary settings (Summit choice, dates for Outlook, etc.).
- **Scrape/Read Loop:** Executes its primary function (e.g., Selenium scraping or API polling).
- **Graceful Reporting:** As scripts run, they write logs and updates directly to the shared `dse.db` SQLite database using the `core/database.py` helper module.

---

## 3. Data Integration & Permanent Memory

### A. The "Permanent Memory" Loop
Unlike traditional scrapers that save to temporary CSVs, the DSE Toolkit uses a **Direct-to-Database** feedback loop:
1.  **Discovery:** A script (e.g., SMS Reader) finds a new message.
2.  **Contextual Linking:** It immediately queries the `contacts` table for a matching phone number.
3.  **Logging:** It inserts the record into `outreach_log`.
4.  **Dashboard Refresh:** Because the web app queries the same DB, the new message appears in the "Activity Log" or "Contact Profile" instantly without any manual sync.

### B. Database Schema Highlights
- **`contacts`:** Stores the master record. Deduplication occurs here on `email` and `phone` during any import or script discovery.
- **`deals`:** A specialized overlay relating a Contact to a specific Summit pipeline (e.g., CMO EU 24).
- **`outreach_log`:** The universal "History" bucket for every ping, reply, and send.

---

## 4. Key Feature Deep-Dives

### 1. Advanced Contact Import
- **Multi-File Support:** You can drag and drop dozens of CSVs at once into the import modal.
- **Folder Upload:** Toggle the "Upload Entire Folder" option to recursively ingest large directory structures of lead data.
- **Deduplication Logic:** Before any contact is saved, the system checks:
    - *Is this Email in the DB?* -> If yes, skip create.
    - *Is this Phone number in the DB?* -> If yes, skip create.

### 2. Deals Dashboard
- **Excel Replacement:** Replaces the legacy `LeadTracka.xlsx`.
- **KPI Grid:** Shows real-time counts for Booked deals, Awaiting First Pitch (FP), and dropped leads.
- **Contact Drill-down:** Click any deal to open the full Contact Profile.

### 3. Contact Profiles & History
- **Unified View:** A single page (`/contacts/<id>`) that merges CRM fields with a chronological timeline of interactions.
- **Cross-Channel:** See SMS and Emails from a single person in one feed, regardless of which tool (Outlook or Zoom) captured them.

---

## 5. Recent Patches & Enhancements
- **Keyword Looping:** The CRM Scraper now allows for entering multiple keyword variations in a single session to broaden search results.
- **Outlook Date Ranges:** When starting the Outlook Reader, you can specify exactly which date window to scan for incoming replies.
- **Zoom SMS Rebuild:** Refactored for better reliability when parsing international phone formats and handling high-volume message bursts.
