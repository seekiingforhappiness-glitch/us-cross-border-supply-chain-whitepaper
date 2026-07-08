"""Single config loader. All modules read tolerances / charge codes / scale here."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import yaml

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_ROOT, "config", "config.yaml")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg


def db_path() -> str:
    """Absolute path to the SQLite db (under app root, gitignored data/)."""
    cfg = load_config()
    rel = cfg["db_path"]
    path = os.path.join(APP_ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
