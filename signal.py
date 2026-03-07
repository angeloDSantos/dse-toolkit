#!/usr/bin/env python3
"""
signal.py — Send control signals to the running scraper.

USAGE
-----
  python signal.py s            skip current event/campaign
  python signal.py c            skip current contact only
  python signal.py p            pause / resume
  python signal.py r            redo previous event/campaign
  python signal.py q            quit cleanly
  python signal.py x fujitsu    exclude all records containing 'fujitsu'
  python signal.py              interactive menu
"""

import os
import sys

SIGNAL_FILE = "scraper_signal.txt"


def send(sig: str):
    with open(SIGNAL_FILE, "w") as f:
        f.write(sig)
    print(f"  Signal sent: {sig!r}")


def menu():
    print()
    print("  " + "=" * 50)
    print("  SCRAPER SIGNAL MENU")
    print("  " + "=" * 50)
    print("    s  — skip current event/campaign")
    print("    c  — skip current contact only")
    print("    p  — pause / resume")
    print("    r  — redo previous event/campaign")
    print("    x  — exclude records by keyword")
    print("    q  — quit cleanly")
    print("  " + "=" * 50)
    print()

    while True:
        raw = input("  Signal (s/c/p/r/x/q): ").strip().lower()
        if raw in ("s", "c", "p", "r", "q"):
            send(raw)
            break
        elif raw == "x":
            kw = input("  Keyword to exclude: ").strip()
            if not kw:
                print("  No keyword entered — cancelled.")
                continue
            send(f"x:{kw}")
            print(f"  Any record containing '{kw}' will be skipped.")
            break
        else:
            print("  Enter one of: s / c / p / r / x / q")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        menu()
    elif len(args) == 1:
        sig = args[0].lower()
        if sig in ("s", "c", "p", "r", "q"):
            send(sig)
        elif sig == "x":
            print("  Usage: python signal.py x <keyword>")
            sys.exit(1)
        else:
            print(f"  Unknown signal '{sig}'. Valid: s / c / p / r / x / q")
            sys.exit(1)
    elif len(args) == 2 and args[0].lower() == "x":
        kw = args[1].strip()
        if not kw:
            print("  Keyword cannot be empty.")
            sys.exit(1)
        send(f"x:{kw}")
        print(f"  Any record containing '{kw}' will be skipped.")
    else:
        print("  Usage:")
        print("    python signal.py s|c|p|r|q")
        print("    python signal.py x <keyword>")
        sys.exit(1)
