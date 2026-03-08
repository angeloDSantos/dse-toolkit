import os
import json
import subprocess
from datetime import datetime

RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(RUNTIME_DIR, "logs")
STATE_DIR = os.path.join(RUNTIME_DIR, "state")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

def _state_path(tool: str) -> str:
    return os.path.join(STATE_DIR, f"{tool}.json")

def write_tool_state(tool: str, **fields):
    """
    Update the state for a tool. Valid statuses:
    starting, waiting_for_user, running, failed, stopped
    """
    path = _state_path(tool)
    current = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            current = {}
    
    current.update(fields)
    current["updated_at"] = datetime.now().isoformat()
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

def read_tool_state(tool: str) -> dict:
    """Read the current state of a tool."""
    path = _state_path(tool)
    if not os.path.exists(path):
        return {"status": "stopped"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "stopped"}

def tail_log(tool: str, n: int = 200) -> list[str]:
    """Return the last N lines from the tool's current log file."""
    state = read_tool_state(tool)
    log_path = state.get("log_path")

    if not log_path or not os.path.exists(log_path):
        return []

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [line.rstrip('\n') for line in lines[-n:]]
    except Exception:
        return []

def launch_tool(tool: str, cmd: list[str], cwd: str | None = None):
    """Launch a tool as a subprocess, writing stdout/stderr to a timestamped log file."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(LOG_DIR, f"{tool}_{ts}.log")
    
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)

    write_tool_state(
        tool,
        status="starting",
        log_path=log_path,
        pid=None,
        started_at=datetime.now().isoformat(),
        error=""  # Clear previous errors
    )

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1
    )

    write_tool_state(
        tool,
        status="running",
        pid=proc.pid,
        log_path=log_path,
    )

    return proc, log_path
