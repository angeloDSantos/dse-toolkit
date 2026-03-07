"""
src/signals.py — File-based signal system for controlling the scraper.

Run ``python signal.py s`` in a second terminal to skip the current
event, ``p`` to pause/resume, ``q`` to quit, etc.
"""

import os
import time
import threading

from config import SIGNAL_FILE


# ─── Signal Exceptions ──────────────────────────────────────────────────────

class SkipEventSignal(Exception):
    pass

class QuitSignal(Exception):
    pass


# ─── Read + clear the signal file ───────────────────────────────────────────

def _read_and_clear_signal() -> str:
    if not os.path.exists(SIGNAL_FILE):
        return ""
    try:
        with open(SIGNAL_FILE, "r") as f:
            sig = f.read().strip().lower()
        os.remove(SIGNAL_FILE)
        return sig
    except Exception:
        return ""


# ─── Controls ────────────────────────────────────────────────────────────────

class Controls:
    """
    Thread-safe controller that polls a signal file for commands.
    Shared across all workers — one signal affects all.
    """

    def __init__(self):
        self._pause              = threading.Event()
        self._skip_contact       = threading.Event()
        self._redo               = threading.Event()
        self._quit_flag          = False
        self._keyword_exclusions = []
        self._lock               = threading.Lock()

    def poll(self):
        sig = _read_and_clear_signal()
        if not sig:
            return
        if sig == "s":
            print("\n  [SIGNAL] Skip event/campaign — stopping current record now.\n", flush=True)
            raise SkipEventSignal()
        elif sig == "q":
            self._quit_flag = True
            print("\n  [SIGNAL] Quit received — stopping after this contact.\n", flush=True)
            raise QuitSignal()
        elif sig == "p":
            self.toggle_pause()
            state = "PAUSED  (send p again to resume)" if self._pause.is_set() else "RESUMED"
            print(f"\n  [SIGNAL] {state}\n", flush=True)
        elif sig == "c":
            self._skip_contact.set()
            print("\n  [SIGNAL] Skip contact received.\n", flush=True)
        elif sig == "r":
            self._redo.set()
            print("\n  [SIGNAL] Redo previous event/campaign received.\n", flush=True)
        elif sig.startswith("x:"):
            kw = sig[2:].strip().lower()
            with self._lock:
                if kw and kw not in self._keyword_exclusions:
                    self._keyword_exclusions.append(kw)
                    print(f"\n  [SIGNAL] Keyword exclusion added: '{kw}'\n", flush=True)
        else:
            print(f"\n  [SIGNAL] Unknown signal '{sig}' — ignored.\n", flush=True)

    # Pause management
    def is_paused(self) -> bool:
        return self._pause.is_set()

    def toggle_pause(self):
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()

    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def wait_if_paused(self):
        while self._pause.is_set():
            time.sleep(0.5)
            self.poll()

    # Skip / redo / quit
    def consume_skip_contact(self) -> bool:
        if self._skip_contact.is_set():
            self._skip_contact.clear()
            return True
        return False

    def consume_redo(self) -> bool:
        if self._redo.is_set():
            self._redo.clear()
            return True
        return False

    def quit_requested(self) -> bool:
        return self._quit_flag

    # Keyword exclusions
    def name_matches_exclusion(self, name: str) -> str:
        n = (name or "").lower()
        with self._lock:
            for kw in self._keyword_exclusions:
                if kw in n:
                    return kw
        return ""

    def list_exclusions(self) -> list:
        with self._lock:
            return list(self._keyword_exclusions)
