"""
zoom_sms/zoom_runtime.py — Startup stability and runtime reporting for Zoom SMS
"""

import os
import sys
from datetime import datetime

# Attempt to configure UTF-8 output to prevent Windows console crashes
def configure_output():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

def log(message: str):
    """Print an ASCII/UTF-8 safe timestamped log."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)

def _get_runtime():
    """Dynamically import the main app runtime layer."""
    try:
        import runtime
        return runtime
    except ImportError:
        # Fallback if running entirely standalone outside DSE toolkit
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        try:
            import runtime
            return runtime
        except ImportError:
            return None

def set_status(status: str, **kwargs):
    """
    Valid statuses: starting, db_scan, waiting_for_user, scraping, saving, complete, failed, stopped
    """
    rt = _get_runtime()
    if rt:
        try:
            rt.write_tool_state("zoom_sms", status=status, **kwargs)
        except Exception as e:
            log(f"[WARNING] Could not write tool state: {e}")

def get_status() -> str:
    """Return the current status, mostly to check if 'stopped_requested'."""
    rt = _get_runtime()
    if rt:
        try:
            state = rt.read_tool_state("zoom_sms")
            return state.get("status", "")
        except Exception:
            return ""
    return ""

def heartbeat():
    """Write a heartbeat to prove the worker isn't frozen."""
    rt = _get_runtime()
    if rt:
        try:
            # write_tool_state already merges and updates 'updated_at'
            rt.write_tool_state("zoom_sms") 
        except Exception:
            pass

def mark_failed(error: str):
    log(f"[ERROR] {error}")
    set_status("failed", error=error)

def mark_complete():
    log("Zoom SMS read complete")
    set_status("complete")

def stop_requested() -> bool:
    return get_status() == "stopped_requested"
