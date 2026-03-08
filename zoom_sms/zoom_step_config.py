"""
zoom_sms/zoom_step_config.py — load/save calibration for one-message step scanning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

DEFAULT_CONFIG_PATH = Path("zoom_step_calibration.json")

REQUIRED_KEYS = {
    "preview_point",
    "scroll_anchor_point",
    "message_pane_point",
    "scroll_amount_per_step",
    "scroll_repeats_per_message",
    "settle_after_click",
    "settle_after_scroll",
}


def default_config() -> Dict[str, Any]:
    return {
        "preview_point": [0, 0],
        "scroll_anchor_point": [0, 0],
        "message_pane_point": [0, 0],
        "scroll_amount_per_step": -2,
        "scroll_repeats_per_message": 2,
        "settle_after_click": 0.8,
        "settle_after_scroll": 0.5,
    }


def validate_config(cfg: Dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"Missing required config keys: {missing}")

    for key in ("preview_point", "scroll_anchor_point", "message_pane_point"):
        pt = cfg[key]
        if not isinstance(pt, list) or len(pt) != 2:
            raise ValueError(f"{key} must be a [x, y] list")

    if not isinstance(cfg["scroll_amount_per_step"], int):
        raise ValueError("scroll_amount_per_step must be int")

    if not isinstance(cfg["scroll_repeats_per_message"], int):
        raise ValueError("scroll_repeats_per_message must be int")


def save_config(cfg: Dict[str, Any], path: str | Path = DEFAULT_CONFIG_PATH) -> Path:
    validate_config(cfg)
    path = Path(path)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Calibration file not found: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    validate_config(cfg)
    return cfg
