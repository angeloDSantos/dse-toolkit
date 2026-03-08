"""
zoom_sms/zoom_step_calibrator.py — interactive calibrator for one-message scrolling.

Usage:
    python zoom_step_calibrator.py
"""

from __future__ import annotations

import time

import pyautogui

from zoom_sms.zoom_step_config import default_config, save_config

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08


def countdown(seconds: int = 4, label: str = "Capturing") -> None:
    for i in range(seconds, 0, -1):
        print(f"{label} in {i}...")
        time.sleep(1)


def capture_point(label: str) -> list[int]:
    print(f"\nMove your mouse to: {label}")
    countdown(4, f"Capturing {label}")
    x, y = pyautogui.position()
    print(f"Captured {label}: ({x}, {y})")
    return [x, y]


def click_point(point: list[int], pause: float = 0.25) -> None:
    x, y = point
    pyautogui.moveTo(x, y, duration=0.12)
    pyautogui.click(x, y)
    time.sleep(pause)


def ask_int(prompt: str, default: int | None = None) -> int:
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("Please enter a whole number.")


def ask_float(prompt: str, default: float | None = None) -> float:
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def test_scroll(anchor_point: list[int], amount: int, repeats: int, settle_after_scroll: float) -> None:
    print(f"\nTesting scroll: amount={amount}, repeats={repeats}")
    click_point(anchor_point, pause=0.3)
    pyautogui.moveTo(anchor_point[0], anchor_point[1], duration=0.05)
    time.sleep(0.1)

    for _ in range(repeats):
        pyautogui.scroll(amount)
        time.sleep(0.08)

    time.sleep(settle_after_scroll)


def calibrate_scroll(anchor_point: list[int], cfg: dict) -> dict:
    print("\n=== CALIBRATE ONE-MESSAGE SCROLL ===")
    print("Goal: find amount + repeat count that moves exactly ONE message preview.")
    print("Use negative values to move downward through the list.")

    last_amount = cfg["scroll_amount_per_step"]
    last_repeats = cfg["scroll_repeats_per_message"]
    last_settle = cfg["settle_after_scroll"]

    while True:
        amount = ask_int(
            f"Scroll amount per step [Enter for {last_amount}]: ",
            default=last_amount,
        )
        repeats = ask_int(
            f"How many repeats per message [Enter for {last_repeats}]: ",
            default=last_repeats,
        )
        settle = ask_float(
            f"Settle after scroll in seconds [Enter for {last_settle}]: ",
            default=last_settle,
        )

        input("\nPress Enter when ready to test...")
        test_scroll(anchor_point, amount, repeats, settle)

        ok = input("Did that advance exactly ONE message preview? [y/n]: ").strip().lower()
        last_amount = amount
        last_repeats = repeats
        last_settle = settle

        if ok == "y":
            cfg["scroll_amount_per_step"] = amount
            cfg["scroll_repeats_per_message"] = repeats
            cfg["settle_after_scroll"] = settle
            print("\nWorking one-message scroll saved in memory.")
            return cfg

        print("Try another combination.\n")


def main() -> None:
    print("=" * 60)
    print("ZOOM STEP CALIBRATOR")
    print("=" * 60)
    print("\nSet Zoom exactly how you plan to use it.")
    print("Recommended:")
    print("- Open Zoom Phone > SMS")
    print("- Put the window in its normal size and position")
    print("- Make sure the left preview list and right message pane are visible")
    print("- Move mouse to top-left corner to abort via PyAutoGUI failsafe")

    cfg = default_config()

    print("\n=== RECORD CLICK POINTS ===")
    cfg["preview_point"] = capture_point("the preview row you want to click every cycle")
    cfg["scroll_anchor_point"] = capture_point("a safe point inside the left preview list for scrolling")
    cfg["message_pane_point"] = capture_point("a safe point inside the right message pane body")

    cfg["settle_after_click"] = ask_float(
        f"Settle after clicking preview/message pane [Enter for {cfg['settle_after_click']}]: ",
        default=cfg["settle_after_click"],
    )

    cfg = calibrate_scroll(cfg["scroll_anchor_point"], cfg)

    out_name = input("\nSave file as [Enter for zoom_step_calibration.json]: ").strip() or "zoom_step_calibration.json"
    out_path = save_config(cfg, out_name)

    print(f"\nSaved calibration to: {out_path.resolve()}")
    print("\nDone.")


if __name__ == "__main__":
    main()
