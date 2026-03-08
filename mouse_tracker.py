import pyautogui
import time

print("Move your mouse. Press Ctrl+C to exit.")
try:
    while True:
        x, y = pyautogui.position()
        print(f"[{x}, {y}]", end="\r", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\nExiting...")
