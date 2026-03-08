# Zoom Step Scanner Usage Guide

This guide explains how to use the new one-message stepping tools.

Files:
- `zoom_step_config.py`
- `zoom_step_calibrator.py`
- `zoom_step_scanner.py`

---

# What these tools do

These tools assume you found a working Zoom behavior where:

- clicking one preview row works
- then doing a specific scroll recipe moves exactly one preview down

Example:
- scroll amount per step = `-2`
- repeats per message = `2`

That means the scanner can:

1. click the current preview
2. copy the message pane
3. parse/save it
4. scroll exactly one preview downward
5. repeat

This is simpler and more reliable than trying to estimate row height every time.

---

# Before you start

Open Zoom desktop and make sure:

- Zoom is on **Phone > SMS**
- the left preview list is visible
- the right message pane is visible
- the window is in the same size and position you plan to use during scanning

These tools depend on fixed click points, so the Zoom window should not move between calibration and scanning.

---

# Step 1 — Put the files in your project

Place these files into your `zoom_sms/` folder:

- `zoom_step_config.py`
- `zoom_step_calibrator.py`
- `zoom_step_scanner.py`

---

# Step 2 — Run the calibrator

```bash
python zoom_sms/zoom_step_calibrator.py
```

---

# Step 3 — Record the three click points

The calibrator will ask you to record:

## A. Preview point
This is the fixed point the scanner will click every cycle.

## B. Scroll anchor point
This is a safe point inside the left preview list where scrolling is sent.

## C. Message pane point
This is the point inside the right message pane that the scanner will click before copying.

---

# Step 4 — Calibrate the one-message scroll recipe

The calibrator lets you test combinations of:

- `scroll_amount_per_step`
- `scroll_repeats_per_message`
- `settle_after_scroll`

Keep trying until one test moves exactly **one** preview downward.

---

# Step 5 — Save the calibration file

Default:

```text
zoom_step_calibration.json
```

Do not move the Zoom window after saving unless you plan to recalibrate.

---

# Step 6 — Run the scanner

```bash
python zoom_sms/zoom_step_scanner.py
```

It will ask for:
- calibration file
- how many previews/messages to iterate
- stop-after-repeated-identical-thread threshold

---

# What the scanner does

For each loop it will:

1. click the saved preview point
2. click the message pane point
3. `Ctrl+A` then `Ctrl+C`
4. save the raw copied thread
5. parse it if `zoom_parser.py` is available
6. persist it to DB if `zoom_store.py` is available and parse is valid
7. click the scroll anchor
8. apply the saved one-message scroll recipe
9. repeat

---

# Output files

Each scan creates a run folder like:

```text
zoom_step_runs/scan_YYYY-MM-DD_HH-MM-SS/
```

Inside you will find:
- raw thread `.txt` files
- parsed `.json` files if parser is available

---

# Recommended first test

1. calibrate
2. run scanner for only `10` messages
3. inspect the run folder
4. confirm the copied threads and parsed output look right

Only then increase to larger runs.
