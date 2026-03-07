"""
src/signals.py — Signal system for scraper control (spec §8).

Two exceptions propagate through the call stack:
  SkipEventSignal  — raised on 's', caught in the record loop
  QuitSignal       — raised on 'q', caught in main / entity wrapper
"""

import os
import time


class SkipEventSignal(Exception):
    """Raised when 's' is received. Propagates up to the record loop."""
    pass


class QuitSignal(Exception):
    """Raised when 'q' is received. Propagates up to main."""
    pass


class Controls:
    """Signal polling and pause management.

    Usage:
        ctrl = Controls(signal_file="scraper_signal.txt")
        # In every loop:
        ctrl.poll()
        ctrl.wait_if_paused()
    """

    def __init__(self, signal_file: str = None):
        if signal_file is None:
            from config import SIGNAL_FILE
            signal_file = SIGNAL_FILE
        self.signal_file = signal_file
        self.paused = False
        self.skip_contact = False
        self.redo = False

    def poll(self):
        """Read and delete the signal file, then act on the signal.

        Called inside every loop — scroll steps, page waits, etc.
        Must be fast: no I/O except the signal file check.
        """
        if not os.path.exists(self.signal_file):
            return

        try:
            with open(self.signal_file, "r") as f:
                sig = f.read().strip().lower()
            os.remove(self.signal_file)
        except (IOError, OSError):
            return

        if not sig:
            return

        if sig == "s":
            print("\n  [SIGNAL] Skip event/campaign received")
            raise SkipEventSignal()

        elif sig == "q":
            print("\n  [SIGNAL] Quit received — finishing current contact then stopping.")
            raise QuitSignal()

        elif sig == "c":
            print("\n  [SIGNAL] Skip current contact")
            self.skip_contact = True

        elif sig == "p":
            self.paused = not self.paused
            state = "PAUSED — send p again to resume" if self.paused else "RESUMED"
            print(f"\n  [SIGNAL] {state}")

        elif sig == "r":
            print("\n  [SIGNAL] Redo — will reprocess previous event/campaign")
            self.redo = True

        elif sig == "status":
            print("\n  [SIGNAL] Status request received")
            # Status is handled by the caller reading ctrl.status_requested
            self.status_requested = True

        elif sig.startswith("x:"):
            keyword = sig[2:].strip()
            if keyword:
                if not hasattr(self, "runtime_exclusions"):
                    self.runtime_exclusions = set()
                self.runtime_exclusions.add(keyword.lower())
                print(f"\n  [SIGNAL] Excluding records containing '{keyword}'")

    def wait_if_paused(self):
        """Block while paused, polling for signals every 0.5s."""
        while self.paused:
            time.sleep(0.5)
            self.poll()  # Listen for p (resume), q (quit), etc.

    def consume_skip_contact(self) -> bool:
        """Check and reset the skip-contact flag."""
        if self.skip_contact:
            self.skip_contact = False
            return True
        return False

    def consume_redo(self) -> bool:
        """Check and reset the redo flag."""
        if self.redo:
            self.redo = False
            return True
        return False

    def consume_status(self) -> bool:
        """Check and reset the status flag."""
        if getattr(self, "status_requested", False):
            self.status_requested = False
            return True
        return False

    def should_exclude_runtime(self, text: str) -> bool:
        """Check if text matches any runtime exclusion keyword."""
        if not hasattr(self, "runtime_exclusions"):
            return False
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.runtime_exclusions)
