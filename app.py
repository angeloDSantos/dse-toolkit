"""
app.py — DSE Toolkit Flask Web Dashboard.

Run with: python app.py
Opens at: http://localhost:5000
"""

import os
import sys
import csv
import json
import io
import subprocess
import threading
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory, session,
)

from core.database import (
    init_db, get_db,
    get_contacts, count_contacts, add_contact, delete_contact,
    get_campaigns, add_campaign, add_contact_to_campaign,
    get_outreach_log, get_outreach_stats, log_outreach,
    get_summit_configs, save_summit_config, get_summit_config,
    get_summit_config_by_id, update_summit_config, delete_summit_config,
    get_scrape_sessions, get_scrape_session, get_scrape_events,
    search_scrape_events,
)

app = Flask(
    __name__,
    template_folder="dashboard/templates",
    static_folder="dashboard/static",
)
app.secret_key = "dse-toolkit-local-key-2024"

LOGIN_PASSWORD = "gds2027"

# Track running background processes
_processes = {}
_process_lock = threading.Lock()


# ─── Auth ────────────────────────────────────────────────────────────────────

@app.before_request
def require_login():
    """Redirect to /login if not authenticated."""
    allowed = ("/login", "/static")
    if request.path.startswith(allowed):
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == LOGIN_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Access denied — invalid key")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Before first request ───────────────────────────────────────────────────

@app.before_request
def _ensure_db():
    init_db()


@app.teardown_appcontext
def _close_db(exc):
    from core.database import close_db
    close_db()


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    stats = get_outreach_stats()
    contacts_count = count_contacts()
    campaigns = get_campaigns()
    recent = get_outreach_log(limit=10)
    return render_template("dashboard.html",
                           stats=stats,
                           contacts_count=contacts_count,
                           campaigns=campaigns,
                           recent_activity=recent)


# ─── Contacts ────────────────────────────────────────────────────────────────

@app.route("/contacts")
def contacts():
    search = request.args.get("q", "")
    page = int(request.args.get("page", 1))
    per_page = 50
    offset = (page - 1) * per_page
    rows = get_contacts(search=search, limit=per_page, offset=offset)
    total = count_contacts(search=search)
    return render_template("contacts.html",
                           contacts=rows, search=search,
                           page=page, total=total, per_page=per_page)


@app.route("/contacts/import", methods=["POST"])
def import_contacts():
    file = request.files.get("csv_file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a .csv file", "error")
        return redirect(url_for("contacts"))

    content = file.stream.read().decode("utf-8-sig")
    count = _import_csv_content(content, source=file.filename)
    flash(f"Imported {count} contacts from {file.filename}", "success")
    return redirect(url_for("contacts"))


# ─── Folder-based import ─────────────────────────────────────────────────

IMPORTS_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "imports")
PROCESSED_DIR   = os.path.join(IMPORTS_DIR, "processed")


def _normalise_header(h):
    """Normalise a CSV header: lowercase, underscores, strip."""
    return h.strip().lower().replace(" ", "_")


def _import_csv_content(content, source=""):
    """Parse CSV text and add contacts to DB. Returns count imported."""
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return 0

    reader.fieldnames = [_normalise_header(h) for h in reader.fieldnames]

    imported = 0
    for row in reader:
        fn = (row.get("first_name") or row.get("firstname") or "").strip()
        ln = (row.get("last_name") or row.get("lastname") or "").strip()
        co = (row.get("company") or row.get("account") or row.get("account_name") or "").strip()
        if not fn:
            continue

        email = (row.get("email") or row.get("secondary_email") or "").strip()
        phone = (row.get("phone") or row.get("mobile") or row.get("number")
                 or row.get("ddi_number") or "").strip()

        # Skip if we already have this exact contact (dedup on email or phone)
        if email or phone:
            db = get_db()
            dup = None
            if email:
                dup = db.execute(
                    "SELECT id FROM contacts WHERE email = ? AND email != ''",
                    (email,)
                ).fetchone()
            if not dup and phone:
                dup = db.execute(
                    "SELECT id FROM contacts WHERE phone = ? AND phone != ''",
                    (phone,)
                ).fetchone()
            if dup:
                continue

        add_contact(
            first_name=fn, last_name=ln, company=co,
            title=(row.get("title") or row.get("job_title") or "").strip(),
            email=email,
            phone=phone,
            linkedin_url=(row.get("linkedin_url") or row.get("linkedin") or "").strip(),
            salesforce_id=(row.get("salesforce_id") or row.get("sf_id")
                           or row.get("contact_url") or "").strip(),
            industry=(row.get("industry") or "").strip(),
            region=(row.get("region") or "").strip(),
            source=(row.get("source") or source).strip(),
        )
        imported += 1
    return imported


@app.route("/contacts/folder")
def folder_imports():
    """List CSV files in data/imports/ ready to import."""
    os.makedirs(IMPORTS_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    files = []
    for fname in sorted(os.listdir(IMPORTS_DIR)):
        if fname.lower().endswith(".csv"):
            fpath = os.path.join(IMPORTS_DIR, fname)
            size = os.path.getsize(fpath)
            # Count rows
            try:
                with open(fpath, encoding="utf-8-sig") as f:
                    row_count = max(0, sum(1 for _ in f) - 1)
            except Exception:
                row_count = 0
            # Read headers
            try:
                with open(fpath, encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    headers = next(reader, [])
            except Exception:
                headers = []

            files.append({
                "name": fname,
                "size_kb": round(size / 1024, 1),
                "rows": row_count,
                "headers": headers,
            })

    # List processed files
    processed = []
    for fname in sorted(os.listdir(PROCESSED_DIR)):
        if fname.lower().endswith(".csv"):
            processed.append(fname)

    return render_template("folder_import.html",
                           files=files, processed=processed,
                           imports_dir=IMPORTS_DIR)


@app.route("/contacts/folder/import/<filename>", methods=["POST"])
def import_from_folder(filename):
    """Import a specific CSV from the imports folder."""
    fpath = os.path.join(IMPORTS_DIR, filename)
    if not os.path.exists(fpath):
        flash(f"File not found: {filename}", "error")
        return redirect(url_for("folder_imports"))

    try:
        with open(fpath, encoding="utf-8-sig") as f:
            content = f.read()
        count = _import_csv_content(content, source=filename)

        # Move to processed
        os.makedirs(PROCESSED_DIR, exist_ok=True)
        dest = os.path.join(PROCESSED_DIR, filename)
        # Avoid overwrite — add timestamp if needed
        if os.path.exists(dest):
            base, ext = os.path.splitext(filename)
            dest = os.path.join(PROCESSED_DIR,
                                f"{base}_{datetime.now().strftime('%H%M%S')}{ext}")
        os.rename(fpath, dest)

        flash(f"Imported {count} contacts from {filename} (moved to processed/)", "success")
    except Exception as e:
        flash(f"Error importing {filename}: {e}", "error")

    return redirect(url_for("folder_imports"))


@app.route("/contacts/folder/import-all", methods=["POST"])
def import_all_from_folder():
    """Import ALL CSVs from the imports folder."""
    os.makedirs(IMPORTS_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    total = 0
    file_count = 0
    for fname in sorted(os.listdir(IMPORTS_DIR)):
        if not fname.lower().endswith(".csv"):
            continue
        fpath = os.path.join(IMPORTS_DIR, fname)
        try:
            with open(fpath, encoding="utf-8-sig") as f:
                content = f.read()
            count = _import_csv_content(content, source=fname)
            total += count
            file_count += 1

            dest = os.path.join(PROCESSED_DIR, fname)
            if os.path.exists(dest):
                base, ext = os.path.splitext(fname)
                dest = os.path.join(PROCESSED_DIR,
                                    f"{base}_{datetime.now().strftime('%H%M%S')}{ext}")
            os.rename(fpath, dest)
        except Exception:
            continue

    flash(f"Imported {total} contacts from {file_count} file(s)", "success")
    return redirect(url_for("folder_imports"))


@app.route("/contacts/delete/<int:cid>", methods=["POST"])
def delete_contact_route(cid):
    delete_contact(cid)
    flash("Contact deleted", "success")
    return redirect(url_for("contacts"))


# ─── Campaigns ───────────────────────────────────────────────────────────────

@app.route("/campaigns")
def campaigns():
    camps = get_campaigns()
    summits = get_summit_configs()
    return render_template("campaigns.html", campaigns=camps, summits=summits)


@app.route("/campaigns/create", methods=["POST"])
def create_campaign():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Campaign name required", "error")
        return redirect(url_for("campaigns"))
    add_campaign(
        name=name,
        summit_name=request.form.get("summit_name", ""),
        venue=request.form.get("venue", ""),
        city=request.form.get("city", ""),
        dates=request.form.get("dates", ""),
        audience=request.form.get("audience", ""),
    )
    flash(f"Campaign '{name}' created", "success")
    return redirect(url_for("campaigns"))


# ─── Outreach ────────────────────────────────────────────────────────────────

@app.route("/outreach")
def outreach():
    tool_status = {}
    with _process_lock:
        for name, proc in _processes.items():
            tool_status[name] = "running" if proc.poll() is None else "stopped"
    return render_template("outreach.html", tool_status=tool_status)


@app.route("/outreach/launch/<tool>", methods=["POST"])
def launch_tool(tool):
    scripts = {
        "scraper": "scraper.py",
        "zoom_sms": os.path.join("zoom_sms", "zoom_reader.py"),
        "outlook_reader": os.path.join("outlook", "outlook_reader.py"),
        "outlook_sender": os.path.join("outlook", "outlook_sender.py"),
    }
    if tool not in scripts:
        flash(f"Unknown tool: {tool}", "error")
        return redirect(url_for("outreach"))

    script = scripts[tool]
    with _process_lock:
        if tool in _processes and _processes[tool].poll() is None:
            flash(f"{tool} is already running", "warning")
            return redirect(url_for("outreach"))

        proc = subprocess.Popen(
            ["python", script],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        _processes[tool] = proc

    flash(f"{tool} launched in new window", "success")
    return redirect(url_for("outreach"))


@app.route("/outreach/stop/<tool>", methods=["POST"])
def stop_tool(tool):
    with _process_lock:
        if tool in _processes and _processes[tool].poll() is None:
            _processes[tool].terminate()
            flash(f"{tool} stopped", "success")
        else:
            flash(f"{tool} is not running", "warning")
    return redirect(url_for("outreach"))


# ─── Messages ───────────────────────────────────────────────────────────────

@app.route("/messages")
@app.route("/replies")
def messages():
    channel = request.args.get("channel", "")
    rows = get_outreach_log(
        channel=channel if channel else None,
        direction="inbound",
        limit=100,
    )
    
    # Simple stats for the analytics grid
    stats = get_outreach_stats()
    
    # More specific counts
    db = get_db()
    
    return render_template(
        "messages.html", 
        messages_list=rows, 
        channel=channel,
        total_messages=stats["total_replies"],
        interested_count=stats["interested"],
        stop_count=stats["stop"],
        meeting_count=db.execute("SELECT COUNT(*) FROM outreach_log WHERE classification IN ('MEETING_REQUEST', 'MEETING')").fetchone()[0],
        outlook_count=db.execute("SELECT COUNT(*) FROM outreach_log WHERE direction='inbound' AND channel='outlook'").fetchone()[0],
        zoom_count=db.execute("SELECT COUNT(*) FROM outreach_log WHERE direction='inbound' AND channel='zoom_sms'").fetchone()[0]
    )


# ─── Activity Log ────────────────────────────────────────────────────────────

@app.route("/activity")
def activity():
    channel = request.args.get("channel", "")
    rows = get_outreach_log(
        channel=channel if channel else None,
        limit=200,
    )
    return render_template("activity.html", activity=rows, channel=channel)


# ─── Settings ────────────────────────────────────────────────────────────────

@app.route("/settings")
def settings():
    summits = get_summit_configs()
    edit_id = request.args.get("edit")
    editing = None
    if edit_id:
        editing = get_summit_config_by_id(int(edit_id))
    return render_template("settings.html", summits=summits, editing=editing)


@app.route("/settings/summit", methods=["POST"])
def save_summit():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Summit name required", "error")
        return redirect(url_for("settings"))

    data = dict(
        venue=request.form.get("venue", ""),
        city=request.form.get("city", ""),
        dates=request.form.get("dates", ""),
        audience=request.form.get("audience", ""),
        tracks=request.form.get("tracks", ""),
        namedrop_companies=request.form.get("namedrop_companies", ""),
        flight_contribution=request.form.get("flight_contribution", ""),
        hotel_nights=request.form.get("hotel_nights", ""),
        zoom_link=request.form.get("zoom_link", ""),
    )

    edit_id = request.form.get("edit_id", "").strip()
    if edit_id:
        update_summit_config(int(edit_id), name=name, **data)
        flash(f"Summit '{name}' updated", "success")
    else:
        save_summit_config(name=name, **data)
        flash(f"Summit '{name}' created", "success")
    return redirect(url_for("settings"))


@app.route("/settings/summit/delete/<int:sid>", methods=["POST"])
def delete_summit(sid):
    config = get_summit_config_by_id(sid)
    name = config["name"] if config else "Summit"
    delete_summit_config(sid)
    flash(f"Summit '{name}' deleted", "success")
    return redirect(url_for("settings"))


# ─── API endpoints for AJAX ─────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    stats = get_outreach_stats()
    stats["contacts"] = count_contacts()
    stats["campaigns"] = len(get_campaigns())
    return jsonify(stats)


@app.route("/api/tool-status")
def api_tool_status():
    status = {}
    with _process_lock:
        for name, proc in _processes.items():
            status[name] = "running" if proc.poll() is None else "stopped"
    return jsonify(status)


# ─── Scraper Routes ─────────────────────────────────────────────────────────

@app.route("/scraper")
def scraper_page():
    """Scraper page — setup form, live feed, or completed results."""
    from src.scraper_runner import is_running, get_active_session_id

    active_id = get_active_session_id()

    # If a scrape is running, show the live feed
    if active_id and is_running():
        session = get_scrape_session(active_id)
        events = get_scrape_events(active_id, since_id=0, limit=500)
        # Reverse so newest is first
        events = list(reversed(events))
        last_id = events[0]["id"] if events else 0
        return render_template("scraper.html",
                               session=session,
                               events=events,
                               last_event_id=last_id,
                               sessions=[],
                               results=[], search="")

    # No active scrape — show setup form + history
    sessions = get_scrape_sessions()
    return render_template("scraper.html",
                           session=None,
                           events=[],
                           last_event_id=0,
                           sessions=sessions,
                           results=[], search="")


@app.route("/scraper/<int:session_id>")
def scraper_session(session_id):
    """View a completed scrape session with search."""
    session = get_scrape_session(session_id)
    if not session:
        flash("Session not found", "error")
        return redirect(url_for("scraper_page"))

    search = request.args.get("q", "")

    # If still running, redirect to main scraper page
    if session["status"] in ("running", "mfa_required"):
        return redirect(url_for("scraper_page"))

    # Search or list all saved contacts from this session
    if search:
        results = search_scrape_events(session_id, query=search, event_type="saved")
    else:
        results = search_scrape_events(session_id, event_type="saved")

    return render_template("scraper.html",
                           session=session,
                           events=[],
                           last_event_id=0,
                           sessions=[],
                           results=results,
                           search=search)


@app.route("/scraper/start", methods=["POST"])
def scraper_start():
    """Start a new scrape from the web UI."""
    from src.scraper_runner import start_scrape, is_running

    if is_running():
        flash("A scrape is already running", "warning")
        return redirect(url_for("scraper_page"))

    scrape_name = request.form.get("scrape_name", "").strip()
    if not scrape_name:
        flash("Scrape name required", "error")
        return redirect(url_for("scraper_page"))

    # Parse keywords
    keywords_raw = request.form.get("keywords", "").strip()
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()] if keywords_raw else []

    # Parse warning exclusions
    exclusions = set()
    if request.form.get("excl_all"):
        exclusions.add("__ALL__")
    else:
        for key in ("dnc", "do_not_email", "do_not_text", "blacklist",
                     "open_opportunity", "yellow_card"):
            if request.form.get(f"excl_{key}"):
                exclusions.add(key)

    mode = request.form.get("mode", "mobile")

    config = {
        "scrape_name": scrape_name,
        "entity_type": request.form.get("entity_type", "events"),
        "mode": mode,
        "keywords": keywords,
        "record_region": request.form.get("record_region", "all"),
        "phone_region": request.form.get("phone_region", "all"),
        "warning_exclusions": exclusions,
        "list_limit": int(request.form.get("list_limit", 500)),
    }

    try:
        session_id = start_scrape(config)
        flash(f"Scrape '{scrape_name}' started (session #{session_id})", "success")
    except Exception as e:
        flash(f"Failed to start scrape: {e}", "error")

    return redirect(url_for("scraper_page"))


@app.route("/scraper/stop", methods=["POST"])
def scraper_stop():
    """Stop the running scrape."""
    from src.scraper_runner import request_stop, is_running

    if is_running():
        request_stop()
        flash("Stop signal sent — scraper will finish current contact then stop", "success")
    else:
        flash("No scrape is running", "warning")

    return redirect(url_for("scraper_page"))


@app.route("/scraper/mfa", methods=["POST"])
def scraper_mfa():
    """Submit MFA code to the running scraper."""
    from src.scraper_runner import submit_mfa_from_web

    code = request.form.get("mfa_code", "").strip()
    if not code:
        flash("MFA code required", "error")
    else:
        submit_mfa_from_web(code)
        flash("MFA code submitted — verifying...", "success")

    return redirect(url_for("scraper_page"))


@app.route("/api/scraper/events")
def api_scraper_events():
    """Polling endpoint for live feed updates."""
    session_id = request.args.get("session_id", 0, type=int)
    since_id = request.args.get("since_id", 0, type=int)

    session = get_scrape_session(session_id)
    events = get_scrape_events(session_id, since_id=since_id, limit=100)

    return jsonify({
        "session": {
            "status": session["status"],
            "contacts_saved": session["contacts_saved"],
            "orders_done": session["orders_done"],
            "skipped": session["skipped"],
            "sponsors_found": session["sponsors_found"],
            "non_delegates": session["non_delegates"],
            "current_record": session["current_record"],
        } if session else None,
        "events": [
            {
                "id": e["id"],
                "event_type": e["event_type"],
                "first_name": e["first_name"],
                "last_name": e["last_name"],
                "company": e["company"],
                "title": e["title"],
                "email": e["email"],
                "phone": e["phone"],
                "record_name": e["record_name"],
                "reason": e["reason"],
                "warnings": e["warnings"],
                "created_at": e["created_at"],
            }
            for e in events
        ],
    })


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print()
    print("=" * 50)
    print("  DSE TOOLKIT — Web Dashboard")
    print("  http://localhost:5000")
    print("=" * 50)
    print()
    app.run(debug=True, port=5000, use_reloader=False)
