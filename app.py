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
    flash, jsonify, send_from_directory,
)

from core.database import (
    init_db, get_db,
    get_contacts, count_contacts, add_contact, delete_contact,
    get_campaigns, add_campaign, add_contact_to_campaign,
    get_outreach_log, get_outreach_stats, log_outreach,
    get_summit_configs, save_summit_config, get_summit_config,
    get_summit_config_by_id, update_summit_config, delete_summit_config,
)

app = Flask(
    __name__,
    template_folder="dashboard/templates",
    static_folder="dashboard/static",
)
app.secret_key = "dse-toolkit-local-key-2024"

# Track running background processes
_processes = {}
_process_lock = threading.Lock()


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
    total = count_contacts()
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
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        flash("CSV has no headers", "error")
        return redirect(url_for("contacts"))

    # Normalise headers
    reader.fieldnames = [h.strip().lower().replace(" ", "_") for h in reader.fieldnames]

    imported = 0
    for row in reader:
        fn = (row.get("first_name") or row.get("firstname") or "").strip()
        ln = (row.get("last_name") or row.get("lastname") or "").strip()
        co = (row.get("company") or row.get("account") or row.get("account_name") or "").strip()
        if not fn:
            continue
        add_contact(
            first_name=fn, last_name=ln, company=co,
            title=(row.get("title") or row.get("job_title") or "").strip(),
            email=(row.get("email") or "").strip(),
            phone=(row.get("phone") or row.get("mobile") or row.get("number") or "").strip(),
            linkedin_url=(row.get("linkedin_url") or row.get("linkedin") or "").strip(),
            salesforce_id=(row.get("salesforce_id") or row.get("sf_id") or "").strip(),
            industry=(row.get("industry") or "").strip(),
            region=(row.get("region") or "").strip(),
            source=(row.get("source") or file.filename).strip(),
        )
        imported += 1

    flash(f"Imported {imported} contacts from {file.filename}", "success")
    return redirect(url_for("contacts"))


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


# ─── Replies ─────────────────────────────────────────────────────────────────

@app.route("/replies")
def replies():
    channel = request.args.get("channel", "")
    rows = get_outreach_log(
        channel=channel if channel else None,
        direction="inbound",
        limit=100,
    )
    return render_template("replies.html", replies=rows, channel=channel)


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
