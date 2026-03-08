"""
zoom_sms/zoom_store.py — Persistence layer for Zoom SMS
"""

import os
import sys
import json
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVO_DB = os.path.join(DATA_DIR, "conversations.db")

def init_archive_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(CONVO_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            phone          TEXT PRIMARY KEY,
            contact_name   TEXT,
            last_message   TEXT,
            direction      TEXT,
            timestamp      TEXT,
            classification TEXT DEFAULT 'UNKNOWN',
            raw_thread     TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            phone     TEXT NOT NULL,
            direction TEXT NOT NULL,
            content   TEXT,
            timestamp TEXT,
            UNIQUE(phone, direction, content)
        )
    """)
    con.commit()
    con.close()


def save_conversation_archive(phone, contact_name, messages, classification="UNKNOWN"):
    """Write a conversation to local conversations.db archive. 
    Returns dict of counts."""
    init_archive_db()
    con = sqlite3.connect(CONVO_DB)
    
    stats = {
        "archive_conversation_written": False,
        "archive_messages_written": 0
    }
    
    try:
        last = messages[-1] if messages else {}
        con.execute("""
            INSERT OR REPLACE INTO conversations
            (phone, contact_name, last_message, direction, timestamp,
             classification, raw_thread)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            phone, contact_name,
            last.get("content", ""),
            last.get("direction", ""),
            last.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            classification,
            json.dumps(messages),
        ))
        stats["archive_conversation_written"] = True
        
        for msg in messages:
            try:
                con.execute("""
                    INSERT OR IGNORE INTO messages (phone, direction, content, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (phone, msg.get("direction", ""),
                      msg.get("content", ""), msg.get("timestamp", "")))
                
                if con.total_changes > 0:
                    stats["archive_messages_written"] += 1
            except Exception:
                pass
        con.commit()
    finally:
        con.close()
        
    return stats


def save_to_app_db(phone, contact_name, messages, classification):
    """Write messages to the core.database outreach_log.
    Returns dict of counts."""
    stats = {
        "app_rows_written": 0,
        "duplicates_skipped": 0
    }
    
    try:
        project_root = os.path.dirname(BASE_DIR)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from core.database import get_db, init_db
        init_db()
        db = get_db()
        
        # Keep track of changes before
        initial_changes = db.total_changes

        for msg in messages:
            content   = msg.get("content", "").strip()
            direction = msg.get("direction", "")
            ts        = msg.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not content:
                continue

            # Need to avoid duplicates
            existing = db.execute(
                "SELECT id FROM outreach_log WHERE phone=? AND direction=? AND message=?",
                (phone, direction, content)
            ).fetchone()
            
            if existing:
                stats["duplicates_skipped"] += 1
                continue

            cls = classification if direction == "inbound" else ""
            db.execute("""
                INSERT INTO outreach_log
                (phone, contact_name, channel, direction, message, classification, timestamp)
                VALUES (?, ?, 'zoom_sms', ?, ?, ?, ?)
            """, (phone, contact_name or "", direction, content, cls, ts))
            
            stats["app_rows_written"] += 1

        db.commit()
    except Exception as exc:
        pass # Not fatal if app DB is missing (standalone mode)
        
    return stats


def persist_conversation(
    phone: str,
    contact_name: str,
    messages: list[dict],
    classification: str = "UNKNOWN",
) -> dict:
    """
    Unified persistence entrypoint.
    Saves to local archive and main app DB, then returns merged stats.
    """
    archive_stats = save_conversation_archive(
        phone=phone,
        contact_name=contact_name,
        messages=messages,
        classification=classification,
    )
    app_stats = save_to_app_db(
        phone=phone,
        contact_name=contact_name,
        messages=messages,
        classification=classification,
    )

    merged = {}
    merged.update(archive_stats)
    merged.update(app_stats)
    return merged


def persist_parsed_conversation(parsed: dict) -> dict:
    """
    Convenience wrapper for parsed conversation dictionaries.
    """
    phone = parsed.get("phone", "")
    if not phone:
        return {
            "archive_conversation_written": False,
            "archive_messages_written": 0,
            "archive_duplicates_skipped": 0,
            "app_db_available": False,
            "app_rows_written": 0,
            "app_duplicates_skipped": 0,
            "app_errors": 0,
        }

    return persist_conversation(
        phone=phone,
        contact_name=parsed.get("contact_name", ""),
        messages=parsed.get("messages", []) or [],
        classification=parsed.get("classification", "UNKNOWN"),
    )


def load_stored_conversations() -> list:
    """Load all conversations from the local archive."""
    init_archive_db()
    con = sqlite3.connect(CONVO_DB)
    try:
        rows = con.execute(
            "SELECT phone, contact_name, last_message, direction, "
            "timestamp, classification FROM conversations ORDER BY timestamp DESC"
        ).fetchall()
        return [
            {"phone": r[0], "contact_name": r[1], "last_message": r[2],
             "direction": r[3], "timestamp": r[4], "classification": r[5]}
            for r in rows
        ]
    finally:
        con.close()
