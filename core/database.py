"""
core/database.py — SQLite database for the DSE system.

Single file database at data/dse.db. All tables for contacts,
campaigns, outreach logging, replies, and summit configs.
"""

import os
import sqlite3
import threading
from datetime import datetime

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "dse.db"
)

_local = threading.local()


def get_db():
    """Get a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def close_db():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None


def init_db():
    """Create all tables if they don't exist."""
    db = get_db()

    db.executescript("""
        -- Contacts master list
        CREATE TABLE IF NOT EXISTS contacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name      TEXT NOT NULL DEFAULT '',
            last_name       TEXT NOT NULL DEFAULT '',
            company         TEXT NOT NULL DEFAULT '',
            title           TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            phone           TEXT DEFAULT '',
            linkedin_url    TEXT DEFAULT '',
            salesforce_id   TEXT DEFAULT '',
            industry        TEXT DEFAULT '',
            region          TEXT DEFAULT '',
            source          TEXT DEFAULT '',
            role_priority   INTEGER DEFAULT 99,
            status          TEXT DEFAULT 'new',
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
        CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);

        -- Campaigns (one per summit run)
        CREATE TABLE IF NOT EXISTS campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            summit_name     TEXT DEFAULT '',
            venue           TEXT DEFAULT '',
            city            TEXT DEFAULT '',
            dates           TEXT DEFAULT '',
            audience        TEXT DEFAULT '',
            status          TEXT DEFAULT 'active',
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- Campaign membership
        CREATE TABLE IF NOT EXISTS campaign_contacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            status          TEXT DEFAULT 'queued',
            added_at        TEXT DEFAULT (datetime('now')),
            UNIQUE(campaign_id, contact_id)
        );

        -- Outreach log (ALL channels unified)
        CREATE TABLE IF NOT EXISTS outreach_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER REFERENCES contacts(id),
            campaign_id     INTEGER REFERENCES campaigns(id),
            channel         TEXT NOT NULL,
            direction       TEXT NOT NULL DEFAULT 'outbound',
            content         TEXT DEFAULT '',
            timestamp       TEXT DEFAULT (datetime('now')),
            status          TEXT DEFAULT 'sent',
            classification  TEXT DEFAULT '',
            contact_name    TEXT DEFAULT '',
            contact_email   TEXT DEFAULT '',
            contact_phone   TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_outreach_contact ON outreach_log(contact_id);
        CREATE INDEX IF NOT EXISTS idx_outreach_channel ON outreach_log(channel);

        -- Summit configurations
        CREATE TABLE IF NOT EXISTS summit_configs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE,
            venue               TEXT DEFAULT '',
            city                TEXT DEFAULT '',
            dates               TEXT DEFAULT '',
            audience            TEXT DEFAULT '',
            tracks              TEXT DEFAULT '',
            namedrop_companies  TEXT DEFAULT '',
            flight_contribution TEXT DEFAULT '',
            hotel_nights        TEXT DEFAULT '',
            zoom_link           TEXT DEFAULT 'https://gdssummits.zoom.us/j/2376325263',
            email_subject       TEXT DEFAULT '',
            email_body          TEXT DEFAULT '',
            attachments_json    TEXT DEFAULT '[]',
            created_at          TEXT DEFAULT (datetime('now'))
        );

        -- Scrape sessions (one per scraper run)
        CREATE TABLE IF NOT EXISTS scrape_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_name     TEXT NOT NULL,
            entity_type     TEXT DEFAULT 'events',
            mode            TEXT DEFAULT 'mobile',
            keywords        TEXT DEFAULT '[]',
            record_region   TEXT DEFAULT 'all',
            phone_region    TEXT DEFAULT 'all',
            warning_excl    TEXT DEFAULT '[]',
            list_limit      INTEGER DEFAULT 500,
            status          TEXT DEFAULT 'running',
            contacts_saved  INTEGER DEFAULT 0,
            ddi_saved       INTEGER DEFAULT 0,
            orders_done     INTEGER DEFAULT 0,
            sponsors_found  INTEGER DEFAULT 0,
            non_delegates   INTEGER DEFAULT 0,
            skipped         INTEGER DEFAULT 0,
            current_record  TEXT DEFAULT '',
            started_at      TEXT DEFAULT (datetime('now')),
            finished_at     TEXT DEFAULT ''
        );

        -- Scrape events (per-contact live feed entries)
        CREATE TABLE IF NOT EXISTS scrape_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL REFERENCES scrape_sessions(id),
            event_type      TEXT NOT NULL,
            first_name      TEXT DEFAULT '',
            last_name       TEXT DEFAULT '',
            company         TEXT DEFAULT '',
            title           TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            phone           TEXT DEFAULT '',
            record_name     TEXT DEFAULT '',
            order_url       TEXT DEFAULT '',
            contact_url     TEXT DEFAULT '',
            reason          TEXT DEFAULT '',
            warnings        TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_scrape_events_session
            ON scrape_events(session_id);
    """)
    db.commit()


# ─── Contact helpers ─────────────────────────────────────────────────────────

def add_contact(first_name, last_name, company, **kwargs):
    db = get_db()
    fields = ["first_name", "last_name", "company"]
    values = [first_name, last_name, company]
    for k in ("title", "email", "phone", "linkedin_url", "salesforce_id",
              "industry", "region", "source", "role_priority"):
        if k in kwargs:
            fields.append(k)
            values.append(kwargs[k])
    placeholders = ", ".join("?" for _ in values)
    cols = ", ".join(fields)
    db.execute(f"INSERT INTO contacts ({cols}) VALUES ({placeholders})", values)
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_contacts(search="", limit=200, offset=0):
    db = get_db()
    if search:
        like = f"%{search}%"
        return db.execute(
            "SELECT * FROM contacts WHERE first_name LIKE ? OR last_name LIKE ? "
            "OR company LIKE ? OR title LIKE ? OR email LIKE ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (like, like, like, like, like, limit, offset)
        ).fetchall()
    return db.execute(
        "SELECT * FROM contacts ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()


def count_contacts(search=""):
    db = get_db()
    if search:
        like = f"%{search}%"
        return db.execute(
            "SELECT COUNT(*) FROM contacts WHERE first_name LIKE ? OR last_name LIKE ? "
            "OR company LIKE ? OR title LIKE ? OR email LIKE ?",
            (like, like, like, like, like)
        ).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]


def delete_contact(contact_id):
    db = get_db()
    db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    db.commit()


# ─── Campaign helpers ────────────────────────────────────────────────────────

def add_campaign(name, **kwargs):
    db = get_db()
    fields = ["name"]
    values = [name]
    for k in ("summit_name", "venue", "city", "dates", "audience"):
        if k in kwargs:
            fields.append(k)
            values.append(kwargs[k])
    placeholders = ", ".join("?" for _ in values)
    cols = ", ".join(fields)
    db.execute(f"INSERT INTO campaigns ({cols}) VALUES ({placeholders})", values)
    db.commit()


def get_campaigns():
    return get_db().execute(
        "SELECT * FROM campaigns ORDER BY id DESC"
    ).fetchall()


def add_contact_to_campaign(campaign_id, contact_id):
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO campaign_contacts (campaign_id, contact_id) VALUES (?, ?)",
        (campaign_id, contact_id)
    )
    db.commit()


# ─── Outreach log helpers ───────────────────────────────────────────────────

def log_outreach(channel, direction, content, **kwargs):
    db = get_db()
    fields = ["channel", "direction", "content"]
    values = [channel, direction, content]
    for k in ("contact_id", "campaign_id", "status", "classification",
              "contact_name", "contact_email", "contact_phone"):
        if k in kwargs:
            fields.append(k)
            values.append(kwargs[k])
    placeholders = ", ".join("?" for _ in values)
    cols = ", ".join(fields)
    db.execute(f"INSERT INTO outreach_log ({cols}) VALUES ({placeholders})", values)
    db.commit()


def get_outreach_log(channel=None, direction=None, limit=100):
    db = get_db()
    where = ["content NOT LIKE 'DNC %'"]
    params = []
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    clause = " WHERE " + " AND ".join(where)
    params.append(limit)
    return db.execute(
        f"SELECT * FROM outreach_log{clause} ORDER BY id DESC LIMIT ?", params
    ).fetchall()


def get_outreach_stats():
    db = get_db()
    return {
        "total_sent": db.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE direction='outbound'"
        ).fetchone()[0],
        "total_replies": db.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE direction='inbound'"
        ).fetchone()[0],
        "interested": db.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE classification='INTERESTED'"
        ).fetchone()[0],
        "not_interested": db.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE classification='NOT_INTERESTED'"
        ).fetchone()[0],
        "stop": db.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE classification='STOP'"
        ).fetchone()[0],
        "by_channel": {
            row[0]: row[1] for row in db.execute(
                "SELECT channel, COUNT(*) FROM outreach_log "
                "WHERE direction='outbound' GROUP BY channel"
            ).fetchall()
        },
    }


# ─── Summit config helpers ──────────────────────────────────────────────────

def save_summit_config(name, **kwargs):
    db = get_db()
    existing = db.execute("SELECT id FROM summit_configs WHERE name = ?", (name,)).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [name]
        db.execute(f"UPDATE summit_configs SET {sets} WHERE name = ?", vals)
    else:
        fields = ["name"] + list(kwargs.keys())
        values = [name] + list(kwargs.values())
        placeholders = ", ".join("?" for _ in values)
        cols = ", ".join(fields)
        db.execute(f"INSERT INTO summit_configs ({cols}) VALUES ({placeholders})", values)
    db.commit()


def get_summit_configs():
    return get_db().execute("SELECT * FROM summit_configs ORDER BY name").fetchall()


def get_summit_config(name):
    return get_db().execute(
        "SELECT * FROM summit_configs WHERE name = ?", (name,)
    ).fetchone()


def get_summit_config_by_id(summit_id):
    return get_db().execute(
        "SELECT * FROM summit_configs WHERE id = ?", (summit_id,)
    ).fetchone()


def update_summit_config(summit_id, **kwargs):
    db = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [summit_id]
    db.execute(f"UPDATE summit_configs SET {sets} WHERE id = ?", vals)
    db.commit()


def delete_summit_config(summit_id):
    db = get_db()
    db.execute("DELETE FROM summit_configs WHERE id = ?", (summit_id,))
    db.commit()


# ─── Scrape session helpers ─────────────────────────────────────────────────

def create_scrape_session(scrape_name, entity_type, mode, keywords,
                          record_region, phone_region, warning_excl, list_limit):
    db = get_db()
    import json as _json
    db.execute(
        "INSERT INTO scrape_sessions "
        "(scrape_name, entity_type, mode, keywords, record_region, "
        " phone_region, warning_excl, list_limit) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (scrape_name, entity_type, mode,
         _json.dumps(keywords), record_region, phone_region,
         _json.dumps(list(warning_excl)), list_limit)
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_scrape_session(session_id, **kwargs):
    db = get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [session_id]
    db.execute(f"UPDATE scrape_sessions SET {sets} WHERE id = ?", vals)
    db.commit()


def get_scrape_session(session_id):
    return get_db().execute(
        "SELECT * FROM scrape_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def get_scrape_sessions():
    return get_db().execute(
        "SELECT * FROM scrape_sessions ORDER BY id DESC"
    ).fetchall()


def add_scrape_event(session_id, event_type, **kwargs):
    db = get_db()
    fields = ["session_id", "event_type"]
    values = [session_id, event_type]
    for k in ("first_name", "last_name", "company", "title", "email",
              "phone", "record_name", "order_url", "contact_url",
              "reason", "warnings"):
        if k in kwargs:
            fields.append(k)
            values.append(kwargs[k])
    placeholders = ", ".join("?" for _ in values)
    cols = ", ".join(fields)
    db.execute(f"INSERT INTO scrape_events ({cols}) VALUES ({placeholders})", values)
    db.commit()


def get_scrape_events(session_id, since_id=0, limit=200):
    return get_db().execute(
        "SELECT * FROM scrape_events WHERE session_id = ? AND id > ? "
        "ORDER BY id ASC LIMIT ?",
        (session_id, since_id, limit)
    ).fetchall()


def search_scrape_events(session_id, query="", event_type="saved"):
    db = get_db()
    if query:
        like = f"%{query}%"
        return db.execute(
            "SELECT * FROM scrape_events WHERE session_id = ? AND event_type = ? "
            "AND (first_name LIKE ? OR last_name LIKE ? OR company LIKE ? OR title LIKE ?) "
            "ORDER BY id DESC",
            (session_id, event_type, like, like, like, like)
        ).fetchall()
    return db.execute(
        "SELECT * FROM scrape_events WHERE session_id = ? AND event_type = ? "
        "ORDER BY id DESC",
        (session_id, event_type)
    ).fetchall()
