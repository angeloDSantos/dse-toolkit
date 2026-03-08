"""
zoom_sms/zoom_step_scanner.py — sequential one-message scanner using saved calibration.

Usage:
    python zoom_step_scanner.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pyautogui
import pyperclip

from zoom_sms.zoom_step_config import load_config

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08

try:
    from zoom_sms.zoom_parser import parse_zoom_thread
    HAS_PARSER = True
except Exception:
    HAS_PARSER = False

try:
    from zoom_sms.zoom_store import persist_parsed_conversation
    HAS_STORE = True
except Exception:
    HAS_STORE = False


def click_point(point: list[int], pause: float = 0.25) -> None:
    x, y = point
    pyautogui.moveTo(x, y, duration=0.12)
    pyautogui.click(x, y)
    time.sleep(pause)


def focus_and_copy_message(message_pane_point: list[int]) -> str:
    pyperclip.copy("")
    click_point(message_pane_point, pause=0.25)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.35)
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.45)
    return pyperclip.paste() or ""


def scroll_one_message(scroll_anchor_point: list[int], amount: int, repeats: int, settle_after_scroll: float) -> None:
    click_point(scroll_anchor_point, pause=0.3)
    pyautogui.moveTo(scroll_anchor_point[0], scroll_anchor_point[1], duration=0.05)
    time.sleep(0.1)

    for _ in range(repeats):
        pyautogui.scroll(amount)
        time.sleep(0.08)

    time.sleep(settle_after_scroll)


def make_run_dir() -> Path:
    run_dir = Path(f"zoom_step_runs/scan_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_raw_thread(run_dir: Path, index: int, raw_text: str) -> Path:
    path = run_dir / f"thread_{index:04d}.txt"
    path.write_text(raw_text, encoding="utf-8")
    return path


def save_parsed_thread(run_dir: Path, index: int, parsed: dict) -> Path:
    path = run_dir / f"thread_{index:04d}.json"
    path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    print("=" * 60)
    print("ZOOM STEP SCANNER")
    print("=" * 60)

    cfg_name = input("Calibration file [Enter for zoom_step_calibration.json]: ").strip() or "zoom_step_calibration.json"
    cfg = load_config(cfg_name)

    max_messages = input("How many messages/previews to iterate? [Enter for 100]: ").strip()
    max_messages = int(max_messages) if max_messages else 100

    duplicate_stop_threshold = input("Stop after how many repeated identical threads? [Enter for 3]: ").strip()
    duplicate_stop_threshold = int(duplicate_stop_threshold) if duplicate_stop_threshold else 3

    run_dir = make_run_dir()
    print(f"\nRun folder: {run_dir.resolve()}")

    preview_point = cfg["preview_point"]
    scroll_anchor_point = cfg["scroll_anchor_point"]
    message_pane_point = cfg["message_pane_point"]
    amount = cfg["scroll_amount_per_step"]
    repeats = cfg["scroll_repeats_per_message"]
    settle_after_click = cfg["settle_after_click"]
    settle_after_scroll = cfg["settle_after_scroll"]

    print("\nLoaded calibration:")
    print(f"  preview_point: {preview_point}")
    print(f"  scroll_anchor_point: {scroll_anchor_point}")
    print(f"  message_pane_point: {message_pane_point}")
    print(f"  scroll_amount_per_step: {amount}")
    print(f"  scroll_repeats_per_message: {repeats}")
    print(f"  settle_after_click: {settle_after_click}")
    print(f"  settle_after_scroll: {settle_after_scroll}")
    print(f"  parser available: {HAS_PARSER}")
    print(f"  store available: {HAS_STORE}")

    input("\nPress Enter when Zoom is ready and the starting preview is visible...")

    prev_text = ""
    repeated = 0
    saved_to_db = 0

    for idx in range(1, max_messages + 1):
        print(f"\n--- Message preview {idx}/{max_messages} ---")

        click_point(preview_point, pause=settle_after_click)
        raw_text = focus_and_copy_message(message_pane_point)
        print(f"Copied {len(raw_text)} chars")

        save_raw_thread(run_dir, idx, raw_text)

        if not raw_text.strip():
            print("Clipboard empty. Stopping.")
            break

        if raw_text == prev_text:
            repeated += 1
            print(f"Repeated identical thread detected ({repeated}/{duplicate_stop_threshold})")
            if repeated >= duplicate_stop_threshold:
                print("Stopping due to repeated identical threads.")
                break
        else:
            repeated = 0
            prev_text = raw_text

        if HAS_PARSER:
            parsed = parse_zoom_thread(raw_text)
            save_parsed_thread(run_dir, idx, parsed)
            print(
                f"Parsed: phone={parsed.get('phone','')} "
                f"msgs={len(parsed.get('messages', []))} "
                f"class={parsed.get('classification','')} "
                f"valid={parsed.get('valid', False)} "
                f"confidence={parsed.get('confidence', 0.0)}"
            )

            if HAS_STORE and parsed.get("valid"):
                stats = persist_parsed_conversation(parsed)
                saved_to_db += 1 if stats.get("archive_conversation_written") else 0
                print(f"Persisted stats: {stats}")
        else:
            print("Parser unavailable; raw text only saved to run folder.")

        if idx < max_messages:
            scroll_one_message(
                scroll_anchor_point=scroll_anchor_point,
                amount=amount,
                repeats=repeats,
                settle_after_scroll=settle_after_scroll,
            )

    print("\nDone.")
    print(f"Raw/parsed thread files saved under: {run_dir.resolve()}")
    print(f"Conversations persisted to DB this run: {saved_to_db}")


if __name__ == "__main__":
    main()
