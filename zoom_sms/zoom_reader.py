"""
zoom_sms/zoom_reader.py - Read and store Zoom Phone SMS conversations.

TWO METHODS (tried in order):
------------------------------------------------------------------------------
1. DIRECT DB SCAN  (fast, silent, no Zoom window needed)
   Uses the same glob search as db_spider.py - scans every .db file under
   BOTH %APPDATA%/Roaming/Zoom/** and %APPDATA%/Local/Zoom/**
   Copies each file to a temp path before reading so Zoom keeps its lock.

2. SCREEN SCRAPE  (supplement / fallback when DB is empty)
   Brings Zoom to the foreground, clicks every conversation row in the left
   list pane, then reads the message thread from the right pane using
   Ctrl+A / Ctrl+C *inside that pane specifically*.
   Scrolls the list to pick up all conversations.

OUTPUT:
   Every conversation is written to:
     • conversations.db      (local archive, same schema as before)
     • core.database outreach_log  (so app.py Messages/Activity pages see it)

USAGE:
   CLI interactive:       python zoom_reader.py
   CLI bulk (from app):   python zoom_reader.py --auto
   Import:                from zoom_sms.zoom_reader import ZoomReader
"""

import os
import re
import glob
import json
import time
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from collections import defaultdict

# Ensure the root project directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -- optional GUI / Win32 libs -------------------------------------------------
try:
    import pyautogui
    import pyperclip
    HAS_GUI = True
    # Failsafe: moving mouse to 0,0 kills script
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.015
except ImportError:
    HAS_GUI = False

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# =============================================================================
# CONFIG
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVO_DB = os.path.join(DATA_DIR, "conversations.db")
CONFIG_PATH = os.path.join(BASE_DIR, "zoom_reader_config.json")

os.makedirs(DATA_DIR, exist_ok=True)

# Screen scrape tuning - adjust if Zoom layout differs on this machine
CONVO_ROW_HEIGHT  = 64      # approximate px height of one conversation row
CONVO_LIST_X_PCT  = 0.17    # horizontal centre of left conversation list (0-1 of window)
CONVO_FIRST_Y_PCT = 0.22    # top of the first conversation row (0-1 of window height)
MSG_PANE_X_PCT    = 0.72    # horizontal centre of the message pane (0-1 of window)
MSG_PANE_Y_PCT    = 0.50    # vertical centre of the message pane (0-1 of window)
CONVO_LOAD_WAIT   = 0.45    # seconds to wait after clicking a conversation row
MAX_SCREEN_CONVOS = 200     # maximum conversations to screen-scrape in one run


from zoom_sms.zoom_store import persist_conversation, load_stored_conversations, init_archive_db
from zoom_sms.zoom_runtime import log, set_status, heartbeat, mark_failed, mark_complete

def load_calibration_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def run_calibration_wizard():
    print("\n  *** ZOOM SMS SETUP WIZARD ***")
    
    cfg = load_calibration_config()
    if cfg:
        print(f"  Existing configuration found: {CONFIG_PATH}")
        ans = input("\n  Do you want to recalibrate? (y/N): ").strip().lower()
        if ans != 'y':
            print("  Keeping existing configuration.\n")
            return

    print("\n  Make sure Zoom is open and visible on screen.")
    input("  Press ENTER when you are ready to start the first 5-second countdown...")

    print("\n  You have 5 seconds to hover your mouse over the MSG PANE (the chat area).")
    print("\n  You have 5 seconds to hover your mouse over the LARGER MESSAGE PANE (the main chat area).")
    for i in range(5, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    mx, my = pyautogui.position()
    print(f"  Saved LARGER MESSAGE PANE point: ({mx}, {my})")
    
    print("\n  You have 5 seconds to hover over the FIRST CHAT PREVIEW (top left of the list).")
    for i in range(5, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    lx, ty = pyautogui.position()
    print(f"  Saved FIRST CHAT PREVIEW point: ({lx}, {ty})")
    
    print("\n  You have 5 seconds to hover over the SECOND CHAT PREVIEW (just below the first).")
    for i in range(5, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    _, by = pyautogui.position()
    
    row_h = abs(by - ty)
    if row_h == 0: 
        row_h = 64
    print(f"  Saved ROW HEIGHT: {row_h}")

    print("\n  Finally, hover your mouse slightly BELOW the chat previews, where the SCROLL LIST is blank.")
    print("  This is used to safely scroll down without clicking a name.")
    for i in range(5, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    sx, sy = pyautogui.position()
    print(f"  Saved SCROLL ANCHOR point: ({sx}, {sy})")
    
    cfg = {
        "msg_x": mx, "msg_y": my,
        "list_x": lx, "first_y": ty,
        "scroll_x": sx, "scroll_y": sy,
        "row_height": row_h
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"  Setup complete! Configuration saved to {CONFIG_PATH}\n")


# =============================================================================
# DATABASE SCANNER
# Uses the same broad glob as db_spider.py - finds every .db Zoom has written
# across both Roaming and Local AppData, not just one named zoomdb.db.
# =============================================================================

def _find_all_zoom_dbs() -> list:
    """
    Glob both AppData/Roaming/Zoom/** and AppData/Local/Zoom/** for every .db
    file Zoom has written - mirrors db_spider.py's search pattern exactly.
    """
    roaming = os.environ.get("APPDATA", "")       # Roaming
    local   = os.environ.get("LOCALAPPDATA", "")  # Local

    found = []
    for base in (roaming, local):
        if not base:
            continue
        pattern = os.path.join(base, "Zoom", "**", "*.db")
        for path in glob.glob(pattern, recursive=True):
            try:
                if os.path.getsize(path) > 0:
                    found.append(path)
            except OSError:
                pass

    # Deduplicate across symlinks / alternate paths
    seen, unique = set(), []
    for p in found:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _has_sms_tables(db_path: str) -> bool:
    """Quick check - does this .db have any message-like tables?"""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(db_path, tmp.name)
        con = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        tables_str = " ".join(
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ).lower()
        con.close()
        os.unlink(tmp.name)
        return any(k in tables_str for k in ("sms", "message", "chat", "im"))
    except Exception:
        return False


def _scan_one_db(db_path: str) -> list:
    """
    Extract message rows from a single Zoom .db file using a temp copy.
    Prints diagnostics rather than silently swallowing errors.
    Returns list of {phone, content, direction, timestamp}.
    """
    results = []

    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(db_path, tmp.name)
    except Exception as e:
        print(f"    [db] Cannot copy {db_path}: {e}")
        return results

    try:
        con = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        all_tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        sms_tables = [t for t in all_tables
                      if any(k in t.lower() for k in ("sms", "message", "chat", "im"))]

        if not sms_tables:
            con.close()
            return results

        for table in sms_tables:
            try:
                col_rows = con.execute(f"PRAGMA table_info({table})").fetchall()
                cols     = [r[1] for r in col_rows]
                cols_low = [c.lower() for c in cols]

                def _pick(*keywords):
                    """Return original-case column name matching first keyword hit."""
                    for c in cols_low:
                        if any(k in c for k in keywords):
                            return cols[cols_low.index(c)]
                    return None

                phone_col = _pick("number", "phone", "recipient",
                                  "peer", "to_number", "callee", "dst")
                body_col  = _pick("content", "body", "message",
                                  "text", "msg", "payload")
                dir_col   = _pick("direction", "is_outgoing", "is_send",
                                  "outgoing", "send_type", "msg_type")
                time_col  = _pick("timestamp", "time", "created_at",
                                  "date", "sent_at", "received_at")

                if not phone_col or not body_col:
                    print(f"    [db] {table}: no phone/body columns - skipping")
                    continue

                # Build positional SELECT so index lookups are safe
                sel = [phone_col, body_col]
                pos = {"phone": 0, "body": 1}
                if dir_col:
                    pos["dir"] = len(sel); sel.append(dir_col)
                if time_col:
                    pos["ts"]  = len(sel); sel.append(time_col)

                rows = con.execute(
                    f"SELECT {', '.join(sel)} FROM {table} "
                    f"ORDER BY rowid DESC LIMIT 10000"
                ).fetchall()

                print(f"    [db] {table}: {len(rows)} rows "
                      f"(phone={phone_col}, body={body_col})")

                for row in rows:
                    phone_raw = str(row[pos["phone"]] or "").strip()
                    content   = str(row[pos["body"]]  or "").strip()
                    if not phone_raw or not content:
                        continue

                    phone = _normalize_phone(phone_raw)
                    if not phone:
                        continue

                    direction = ""
                    if "dir" in pos:
                        raw_d = str(row[pos["dir"]] or "").strip().lower()
                        if raw_d in ("1", "true", "send", "sent", "outgoing", "out"):
                            direction = "outbound"
                        elif raw_d in ("0", "false", "recv", "received",
                                       "incoming", "in"):
                            direction = "inbound"

                    timestamp = str(row[pos["ts"]] or "").strip() if "ts" in pos else ""

                    results.append({
                        "phone":     phone,
                        "content":   content,
                        "direction": direction,
                        "timestamp": timestamp,
                    })

            except Exception as e:
                print(f"    [db] Error reading table {table}: {e}")
                continue

        con.close()

    except Exception as e:
        print(f"  [db] Error opening {db_path}: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return results


def scan_zoom_databases() -> dict:
    """
    Scan all Zoom .db files under Roaming + Local AppData.
    Returns {phone: {contact_name, messages, classification, last_reply, message_count}}.
    Writes all results to conversations.db and outreach_log.
    """
    all_dbs = _find_all_zoom_dbs()
    if not all_dbs:
        print("  [db] No Zoom .db files found.")
        print(f"       Searched: {os.environ.get('APPDATA', '?')}\\Zoom\\**\\*.db")
        print(f"       Searched: {os.environ.get('LOCALAPPDATA', '?')}\\Zoom\\**\\*.db")
        return {}

    print(f"  [db] Found {len(all_dbs)} Zoom .db file(s) total")

    all_messages = []
    for path in all_dbs:
        if not _has_sms_tables(path):
            continue
        print(f"  [db] Scanning: {path}")
        msgs = _scan_one_db(path)
        all_messages.extend(msgs)
        print(f"       -> {len(msgs)} message rows")

    if not all_messages:
        print("  [db] No SMS messages found in any database.")
        return {}

    print(f"  [db] Total rows extracted: {len(all_messages)}")

    by_phone = defaultdict(list)
    for msg in all_messages:
        by_phone[msg["phone"]].append(msg)

    conversations = {}
    for phone, msgs in by_phone.items():
        msgs.sort(key=lambda m: m.get("timestamp", ""))

        contact_name = ""
        for msg in msgs:
            if msg.get("direction") == "outbound":
                contact_name = _extract_name_from_template(msg["content"])
                if contact_name:
                    break

        last_inbound = next(
            (m["content"] for m in reversed(msgs)
             if m.get("direction") == "inbound"),
            ""
        )
        classification = classify_reply(last_inbound) if last_inbound else "NO_REPLY"

        if classification == "STOP":
            continue

        conversations[phone] = {
            "contact_name":  contact_name,
            "messages":      msgs,
            "classification": classification,
            "last_reply":    last_inbound,
            "message_count": len(msgs),
        }

        persist_conversation(phone, contact_name, msgs, classification)

    print(f"  [db] Saved {len(conversations)} conversations.")
    return conversations


# =============================================================================
# WINDOW HELPERS
# =============================================================================

def _get_zoom_hwnd():
    if not HAS_WIN32:
        return None
    hwnd = win32gui.FindWindow(None, "Zoom")
    if hwnd and win32gui.IsWindowVisible(hwnd):
        return hwnd
    matches = []
    def _cb(h, _):
        if win32gui.IsWindowVisible(h) and "Zoom" in win32gui.GetWindowText(h):
            matches.append(h)
    win32gui.EnumWindows(_cb, None)
    return matches[0] if matches else None


def _bring_zoom_to_front() -> bool:
    """Focus the Zoom window. Returns True if successful."""
    hwnd = _get_zoom_hwnd()
    if hwnd and HAS_WIN32:
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.7)
            return True
        except Exception as e:
            print(f"  [win32] SetForegroundWindow: {e}")

    # Fallback - PowerShell AppActivate works without win32
    try:
        subprocess.run(
            ["powershell", "-Command",
             "(New-Object -ComObject WScript.Shell).AppActivate('Zoom')"],
            capture_output=True, timeout=5
        )
        time.sleep(1.3)
        return True
    except Exception as e:
        print(f"  [screen] Could not focus Zoom: {e}")
        return False


def _zoom_rect() -> tuple:
    """Return (left, top, right, bottom) of the Zoom window."""
    if HAS_WIN32:
        hwnd = _get_zoom_hwnd()
        if hwnd:
            try:
                return win32gui.GetWindowRect(hwnd)
            except Exception:
                pass
    w, h = pyautogui.size()
    return (0, 0, w, h)


# =============================================================================
# SCREEN SCRAPE - MESSAGE PANE
#
# THE KEY FIX vs the old code:
#   Old code did pyautogui.hotkey("ctrl", "a") immediately after focusing Zoom.
#   This selected whatever widget happened to have keyboard focus - usually the
#   navigation sidebar or search box, not the message thread.
#
#   The fix: click at MSG_PANE_X_PCT (72% across the window) FIRST to give the
#   message thread area keyboard focus, then Ctrl+A selects only its content.
# =============================================================================

def _read_message_pane() -> str:
    """
    Click inside the message pane then Ctrl+A / Ctrl+C.
    Returns the clipboard text, or '' on failure.
    """
    cfg = load_calibration_config()
    if cfg and "msg_x" in cfg:
        click_x = cfg["msg_x"]
        click_y = cfg["msg_y"]
    else:
        left, top, right, bottom = _zoom_rect()
        w = right  - left
        h = bottom - top
        click_x = left + int(w * MSG_PANE_X_PCT)
        click_y = top  + int(h * MSG_PANE_Y_PCT)

    pyperclip.copy("")
    pyautogui.click(click_x, click_y)
    time.sleep(0.08)

    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.25)

    text = pyperclip.paste()
    return text if text and len(text) > 15 else ""


# =============================================================================
# SCREEN SCRAPE - CONVERSATION LIST NAVIGATION
# =============================================================================

def _click_conversation_row(visible_row: int,
                             left: int, top: int,
                             win_w: int, win_h: int):
    """
    Click a row in the left conversation list.
    visible_row 0 = first visible entry, 1 = second, etc.
    When the target would be below the window, scroll the list first.
    """
    cfg = load_calibration_config()
    if cfg and "list_x" in cfg:
        list_x   = cfg["list_x"]
        first_y  = cfg["first_y"]
        row_h    = cfg["row_height"]
        scroll_x = cfg.get("scroll_x", list_x)
        scroll_y = cfg.get("scroll_y", first_y + row_h * 5)
        list_btm = top + win_h - 40
    else:
        list_x   = left + int(win_w * CONVO_LIST_X_PCT)
        first_y  = top  + int(win_h * CONVO_FIRST_Y_PCT)
        row_h    = CONVO_ROW_HEIGHT
        scroll_x = list_x
        scroll_y = top + int(win_h * 0.55)
        list_btm = top  + win_h - 40   # leave 40px margin at bottom

    target_y = first_y + visible_row * row_h

    if target_y > list_btm:
        # Scroll the list pane down 3 steps to reveal more rows
        pyautogui.moveTo(scroll_x, scroll_y)
        for _ in range(3):
            pyautogui.scroll(-3)
            time.sleep(0.03)
        # After scroll, click the first visible slot (now shows new rows)
        pyautogui.click(list_x, first_y)
    else:
        pyautogui.click(list_x, target_y)

    time.sleep(CONVO_LOAD_WAIT)


# =============================================================================
# SCREEN SCRAPE - TEXT PARSER
# =============================================================================

def _parse_pane_text(raw: str) -> dict:
    """
    Parse raw text copied from a Zoom SMS message pane.

    Typical format:
        +14155550100
        Today 2:35 PM
        Hey James, we're hosting a summit ...   ← our outbound
        Today 3:01 PM
        Sure sounds interesting                  ← their inbound reply
    """
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    # -- Phone number ------------------------------------------------------
    phone = ""
    for line in lines[:15]:
        m = re.search(r"\+?\d[\d\s\-\(\)]{8,}", line)
        if m:
            cand = _normalize_phone(m.group(0))
            if cand:
                phone = cand
                break

    # -- Split into messages at timestamp markers --------------------------
    time_re = re.compile(
        r"^(?:Today|Yesterday|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+\d{1,2}(?:,?\s*\d{4})?)"
        r"[\s,]+\d{1,2}:\d{2}(?:\s*[AP]M)?",
        re.IGNORECASE,
    )
    junk = {"sms", "phone", "zoom phone", "zoom", "message", "reply", "send"}

    messages   = []
    buf        = []
    current_ts = ""
    current_sender = "Unknown"
    
    # Text right before a timestamp is usually the sender's name in Zoom's copy-paste format.
    # Ex:
    # You
    # Today 2:35 PM
    # Hey James...
    
    prev_line = ""

    for line in lines:
        if line.lower() in junk:
            continue
            
        if time_re.match(line):
            if buf:
                messages.append({
                    "content":   " ".join(buf).strip(),
                    "direction": "outbound" if current_sender == "You" else "inbound",
                    "sender":    current_sender,
                    "timestamp": current_ts,
                })
                buf = []
            current_ts = line
            
            # The sender name is often the line immediately preceding the timestamp
            # If the prev_line looks like a phone number, or "You", or an ID like "6504827"
            if prev_line and not time_re.match(prev_line) and prev_line.lower() not in junk:
                current_sender = prev_line
                
            continue
            
        buf.append(line)
        prev_line = line

    if buf:
        # Before saving the last buffer, remove the sender name if it's the very last line we added 
        # (which happens because we just buffer everything until the *next* timestamp, and the sender name for the *next* message gets caught at the end of the *current* buffer)
        content_lines = buf
        
        # We need a cleaner way to assemble: we actually over-captured the next sender in the buffer above.
        # Let's fix the whole buffer at the end:
    
    # Let's rebuild the extraction to be simpler:
    messages = []
    current_msg = {"sender": "Unknown", "timestamp": "", "content": []}
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.lower() in junk:
            i += 1
            continue
            
        # Is this a timestamp?
        if time_re.match(line):
            # If we have an active message building, save it
            if current_msg["content"]:
                # The very last line of 'content' is actually the sender for THIS new timestamp
                content_list = current_msg["content"]
                sender_name = content_list.pop() if len(content_list) > 0 else "Unknown"
                
                # Save the *previous* message now that we've popped the next sender off its tail
                msg_text = " ".join(content_list).strip()
                if msg_text:
                    messages.append({
                        "content": msg_text,
                        "direction": "outbound" if current_msg["sender"] == "You" else "inbound",
                        "sender": current_msg["sender"],
                        "timestamp": current_msg["timestamp"]
                    })
                    
                # Setup the *new* message
                current_msg = {"sender": sender_name, "timestamp": line, "content": []}
            else:
                # First timestamp we've found
                sender_name = prev_line if i > 0 else "Unknown"
                current_msg = {"sender": sender_name, "timestamp": line, "content": []}
        else:
            current_msg["content"].append(line)
            
        i += 1

    # Don't forget the last message
    if current_msg["content"]:
        content_list = current_msg["content"]
        msg_text = " ".join(content_list).strip()
        if msg_text:
            messages.append({
                "content": msg_text,
                "direction": "outbound" if current_msg["sender"] == "You" else "inbound",
                "sender": current_msg["sender"],
                "timestamp": current_msg["timestamp"]
            })

    # -- Infer direction + extract contact name ----------------------------
    contact_name = ""
    for msg in messages:
        name = _extract_name_from_template(msg["content"])
        if name:
            msg["direction"] = "outbound"
            contact_name     = name

    # Fallback direction inference if sender wasn't "You" or clearly captured
    found_out = False
    for msg in messages:
        if msg["direction"] == "outbound":
            found_out = True
            continue
        if found_out and not msg["direction"]:
            # Short messages are replies; long ones are probably our templates
            msg["direction"] = "inbound" if len(msg["content"]) < 250 else "outbound"

    last_reply = next(
        (m["content"] for m in reversed(messages) if m.get("direction") == "inbound"),
        ""
    )
    
    # If the absolute last message in the thread is inbound, we are AWAITING_REPLY.
    last_msg_overall = messages[-1] if messages else None
    
    if last_msg_overall and last_msg_overall.get("direction") == "inbound":
        classification = "AWAITING_REPLY"
    else:
        classification = classify_reply(last_reply) if last_reply else "NO_REPLY"

    return {
        "phone":          phone,
        "contact_name":   contact_name,
        "messages":       messages,
        "classification": classification,
        "last_reply":     last_reply,
    }


def _scroll_list_down(left: int, top: int, win_w: int, win_h: int):
    """Scroll the conversation list down by exactly one row (2 notches)."""
    cfg = load_calibration_config()
    if cfg and "list_x" in cfg:
        scroll_x = cfg.get("scroll_x", cfg["list_x"])
        scroll_y = cfg.get("scroll_y", cfg["first_y"] + cfg["row_height"] * 5)
    else:
        scroll_x = left + int(win_w * CONVO_LIST_X_PCT)
        scroll_y = top + int(win_h * 0.55)
        
    pyautogui.moveTo(scroll_x, scroll_y)
    for _ in range(2):
        pyautogui.scroll(-2)
        time.sleep(0.015)

# =============================================================================
# SCREEN SCRAPE - BULK LOOP
# =============================================================================

def screen_scrape_all(max_convos: int = MAX_SCREEN_CONVOS) -> list:
    """
    Iterate through every conversation in Zoom's SMS left-pane list.

    Steps:
      1. Bring Zoom to the front.
      2. Click the first conversation row.
      3. Read the message pane (click pane -> Ctrl+A -> Ctrl+C).
      4. Parse + save.
      5. Click the next row.
      6. Stop when the pane content stops changing (end of list).
    """
    if not HAS_GUI:
        print("  [screen] pyautogui / pyperclip not installed - skipping.")
        return []

    print("  [screen] Bringing Zoom to the front...")
    if not _bring_zoom_to_front():
        print("  [screen] Zoom not found. Is it running?")
        return []

    time.sleep(0.6)
    left, top, right, bottom = _zoom_rect()
    win_w = right  - left
    win_h = bottom - top
    print(f"  [screen] Zoom window: ({left},{top}) -> ({right},{bottom})")

    results     = []
    seen_phones = set()
    prev_raw    = ""
    no_change   = 0
    visible_row = 0    # row index within the currently visible list     print("  [screen] Starting continuous bulk scan until list end...")
    
    try:
        _click_conversation_row(0, left, top, win_w, win_h)

        i = 0
        while True:
            # 1. Read current pane()
            raw = _read_message_pane()

            if not raw:
                # Give it a tiny bit extra time and try one more time
                time.sleep(0.4)
                raw = _read_message_pane()
                
            if not raw:
                print(f"  [screen] Empty pane at iteration {i} - stopping.")
                break

            # End-of-list detection: pane text unchanged 3x in a row
            if raw[:80] == prev_raw[:80]:
                no_change += 1
                if no_change >= 3:
                    print("  [screen] Pane unchanged 3x - reached end of list.")
                    break
                # Loading delay? Check the next row
                _scroll_list_down(left, top, win_w, win_h)
                _click_conversation_row(0, left, top, win_w, win_h)
                continue

            no_change = 0
            prev_raw  = raw
            parsed    = _parse_pane_text(raw)
            phone     = parsed.get("phone", "")

            if phone and phone not in seen_phones:
                seen_phones.add(phone)
                name = parsed.get("contact_name", "")
                cls  = parsed.get("classification", "UNKNOWN")
                nmsg = len(parsed.get("messages", []))
                
                if cls == "STOP":
                    print(f"  [screen] [{i+1:>3}] {phone:<16} STOP (Opt-out detected, skipping entirely)")
                else:
                    print(f"  [screen] [{i+1:>3}] {phone:<16} {name or '-':<15} "
                          f"{cls:<14} {nmsg} msgs")
                    persist_conversation(phone, name, parsed["messages"], cls)
                    results.append(parsed)
            elif not phone:
                print(f"  [screen] [{i+1:>3}] No phone number extracted - skipping.")

            # Scroll list down exactly one row and click the top slot again
            _scroll_list_down(left, top, win_w, win_h)
            _click_conversation_row(0, left, top, win_w, win_h)
            i += 1

    except pyautogui.FailSafeException:
        print("\n  [screen] FAILSAFE TRIGGERED (Mouse moved to a corner). Aborting scraping phase early.\n")
        log("[screen] FAILSAFE TRIGGERED. Scraping aborted.")

    print(f"  [screen] Done - {len(results)} new conversations captured.")
    return results


# =============================================================================
# SINGLE CONVERSATION READ
# =============================================================================

def read_current_conversation(auto: bool = False) -> dict:
    """Read only the currently visible conversation from screen."""
    if not HAS_GUI:
        print("  pyautogui not available - pip install pyautogui pyperclip")
        return {}

    if auto:
        _bring_zoom_to_front()
    else:
        print("  Navigate to the Zoom conversation you want to read.")
        input("  Press ENTER when ready...")

    raw = _read_message_pane()
    if not raw:
        print("  Could not read message pane (clipboard empty).")
        return {}

    parsed = _parse_pane_text(raw)
    if parsed.get("phone"):
        persist_conversation(
            parsed["phone"], parsed["contact_name"],
            parsed["messages"], parsed["classification"],
            runtime_log=log
        )
    return parsed


# =============================================================================
# HELPERS
# =============================================================================

def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("+"):
        d = re.sub(r"\D+", "", s)
        return ("+" + d) if len(d) >= 7 else ""
    d = re.sub(r"\D+", "", s)
    if len(d) == 10:                  return "+1" + d
    if len(d) == 11 and d[0] == "1": return "+"  + d
    if len(d) >= 7:                   return "+"  + d
    return ""


def _extract_name_from_template(text: str) -> str:
    """Pull first name from 'Hey James,' / 'Hi Dr Smith,' style templates."""
    m = re.match(
        r"(?:Hey|Hi|Hello|Dear)\s+(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?)?\s*"
        r"([A-Z][a-z]{1,25})",
        (text or "").strip(),
        re.IGNORECASE,
    )
    return m.group(1).capitalize() if m else ""


_REPLY_CLASSES = {
    "STOP": [
        "stop", "unsubscribe", "opt out", "remove me", "do not text",
        "dont text", "don't text", "no more", "wrong number", "remove",
    ],
    "INTERESTED": [
        "interested", "sounds good", "tell me more", "love to", "yes",
        "sure", "sign me up", "count me in", "send me", "i'm in", "im in",
        "definitely", "absolutely", "would love",
    ],
    "NOT_INTERESTED": [
        "not interested", "no thanks", "no thank you", "pass", "decline",
        "not for me", "wrong person", "not relevant",
    ],
    "MEETING": [
        "call", "meeting", "schedule", "available", "free",
        "let's chat", "let's talk", "jump on", "happy to discuss",
    ],
}


def classify_reply(text: str) -> str:
    t = (text or "").lower()
    for cls in ["STOP", "NOT_INTERESTED", "MEETING", "INTERESTED"]:
        if any(kw in t for kw in _REPLY_CLASSES[cls]):
            return cls
    return "UNKNOWN"


# =============================================================================
# ZoomReader CLASS  (public API used by app.py)
# =============================================================================

class ZoomReader:
    """High-level interface - used by app.py and other importers."""

    def __init__(self):
        init_archive_db()

    def run_full_scan(self) -> dict:
        """
        Full pipeline: DB scan first, then screen scrape.
        Called by app.py when launched with --auto.
        """
        print("\n  == ZOOM SMS FULL SCAN ==\n")
        results = {}

        print("  Phase 1 - Direct database scan")
        db_res = scan_zoom_databases()
        results.update(db_res)
        print(f"  DB scan: {len(db_res)} conversations\n")

        if HAS_GUI:
            print("  Phase 2 - Screen scrape (supplements DB)")
            screen_res = screen_scrape_all()
            added = sum(
                1 for r in screen_res
                if r.get("phone") and r["phone"] not in results
                and not results.update({r["phone"]: {
                    "contact_name":  r.get("contact_name", ""),
                    "messages":      r.get("messages", []),
                    "classification": r.get("classification", "UNKNOWN"),
                    "last_reply":    r.get("last_reply", ""),
                    "message_count": len(r.get("messages", [])),
                }})
            )
            print(f"  Screen scan added {added} new conversation(s)\n")
        else:
            print("  Phase 2 - Skipped (pyautogui not installed)\n")

        print(f"  Total saved: {len(results)}")
        return results

    def scan_zoom_database(self) -> dict:
        return scan_zoom_databases()

    def read_screen_conversation(self, auto: bool = False) -> dict:
        return read_current_conversation(auto=auto)

    def scrape_all_conversations(self) -> list:
        if not _bring_zoom_to_front():
            print("  Cannot focus Zoom - is it running?")
            return []
        return screen_scrape_all()

    def get_stored_conversations(self) -> list:
        return load_stored_conversations()

    def get_replies_only(self) -> list:
        return [c for c in load_stored_conversations()
                if c.get("direction") == "inbound" and c.get("last_message")]


# =============================================================================
# CLI
# =============================================================================

def _print_table(convos: dict):
    from collections import Counter
    print(f"\n  {'Phone':<16} {'Name':<15} {'Msgs':>4}  {'Class':<16} {'Last reply'}")
    print(f"  {'-'*16} {'-'*15} {'-'*4}  {'-'*16} {'-'*35}")
    for phone, data in sorted(convos.items(),
                              key=lambda x: x[1].get("classification", "")):
        name = (data.get("contact_name") or "-")[:14]
        cnt  = data.get("message_count", len(data.get("messages", [])))
        cls  = (data.get("classification") or "-")[:15]
        last = (data.get("last_reply") or "-")[:35]
        print(f"  {phone:<16} {name:<15} {cnt:>4}  {cls:<16} {last}")
    counts = Counter(d.get("classification", "-") for d in convos.values())
    print("\n  Summary:")
    for cls, n in sorted(counts.items()):
        print(f"    {cls:<16} - {n}")
    print()


def main():
    # If launched explicitly for setup wizard
    is_wizard_run = False
    if "setup_wizard" in sys.argv:
        is_wizard_run = True
        run_calibration_wizard()
        
    print()
    print("=" * 58)
    print("  ZOOM SMS READER")
    print("=" * 58)

    if is_wizard_run:
        print("\n  [screen] Calibration complete. Starting bulk scan automatically...")
        reader = ZoomReader()
        results = reader.run_full_scan()
        _print_table(results) if results else print("  Nothing found.\n")
        input("  Press ENTER to close window...")
        return

    # app.py launches with --auto - run full pipeline then exit
    if "--auto" in sys.argv:
        if "--prompt-config" in sys.argv:
            ans = input("\n  Do you want to recalibrate the Zoom window? (y/N): ").strip().lower()
            if ans == 'y':
                run_calibration_wizard()

        set_status("starting")
        
        # Preflight checks
        db_paths = _find_all_zoom_dbs()
        if not db_paths and not HAS_GUI:
            error_msg = "Preflight Failed: No Zoom DBs found and GUI automation not available."
            log(error_msg)
            mark_failed(error_msg)
            return

        set_status("scraping") # or running, map it to scraper
        
        try:
            reader = ZoomReader()
            log("[AUTO] Full scan (DB + screen scrape)...")
            results = reader.run_full_scan()
            _print_table(results) if results else log("No conversations found.")
            set_status("stopped")
        except Exception as e:
            import traceback
            traceback.print_exc()
            mark_failed(f"Runtime error: {e}")
        return

    reader = ZoomReader()

    print("\n  Options:")
    print("  1) Full scan - DB + screen (recommended)")
    print("  2) DB scan only              (no Zoom window needed)")
    print("  3) Screen - iterate all conversations")
    print("  4) Screen - read current visible conversation only")
    print("  5) View stored conversations")
    print("  6) View reply conversations only")
    print("  7) Calibrate screen coordinates (Wizard)")
    print()

    choice = input("  -> ").strip()

    if choice == "7":
        run_calibration_wizard()
        
    elif choice == "1":
        results = reader.run_full_scan()
        _print_table(results) if results else print("  Nothing found.\n")

    elif choice == "2":
        print("\n  Scanning Zoom databases...")
        convos = reader.scan_zoom_database()
        _print_table(convos) if convos else print("  No conversations found.\n")

    elif choice == "3":
        print("\n  Starting bulk screen scrape...")
        convos = reader.scrape_all_conversations()
        if convos:
            from collections import Counter
            counts = Counter(c["classification"] for c in convos)
            print(f"\n  Scraped {len(convos)} conversations:")
            for cls, n in sorted(counts.items()):
                print(f"    {cls:<16} - {n}")
        else:
            print("  Nothing scraped.\n")

    elif choice == "4":
        result = reader.read_screen_conversation()
        if result:
            print(f"\n  Phone      : {result.get('phone', '-')}")
            print(f"  Contact    : {result.get('contact_name', '-')}")
            print(f"  Class      : {result.get('classification', '-')}")
            print(f"  Messages   : {len(result.get('messages', []))}")
            if result.get("last_reply"):
                print(f"  Last reply : {result['last_reply'][:100]}")
        else:
            print("  Could not read conversation.")

    elif choice == "5":
        convos = reader.get_stored_conversations()
        if not convos:
            print("  No stored conversations.\n")
            return
        print(f"\n  {len(convos)} stored conversations:\n")
        for c in convos[:50]:
            name = (c.get("contact_name") or "-")[:14]
            last = (c.get("last_message") or "-")[:50]
            print(f"  {c['phone']:<16} {name:<15} [{c['classification']:<14}] {last}")

    elif choice == "6":
        replies = reader.get_replies_only()
        if not replies:
            print("  No reply conversations found.\n")
            return
        print(f"\n  {len(replies)} conversations with replies:\n")
        for c in replies:
            name = (c.get("contact_name") or "-")[:14]
            last = (c.get("last_message") or "-")[:50]
            print(f"  {c['phone']:<16} {name:<15} [{c['classification']:<14}] {last}")

    print()


if __name__ == "__main__":
    main()
