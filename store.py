"""
store.py — the repository lives in a `data/` subfolder next to this file.

No database. Records are held in one JSON file, rewritten atomically on every
change, with a timestamped copy kept in data/backups/ so a bad edit is always
recoverable. Upload the workbook once; from then on the app reads data/tenders.json.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
TENDERS_FILE = DATA_DIR / "tenders.json"
META_FILE = DATA_DIR / "meta.json"

KEEP_BACKUPS = 20


def ensure_dirs() -> None:
    """Create data/ and data/backups/ on first use."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def has_data() -> bool:
    return TENDERS_FILE.exists() and TENDERS_FILE.stat().st_size > 2


def load_tenders() -> list[dict[str, Any]]:
    """Return every stored tender, or an empty list before the first import."""
    if not has_data():
        return []
    try:
        with TENDERS_FILE.open(encoding="utf-8") as fh:
            rows = json.load(fh)
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, OSError):
        # A corrupt file should not lose the repository — fall back to the newest backup.
        for backup in sorted(BACKUP_DIR.glob("tenders_*.json"), reverse=True):
            try:
                with backup.open(encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
        return []


def _prune_backups() -> None:
    backups = sorted(BACKUP_DIR.glob("tenders_*.json"), reverse=True)
    for old in backups[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)


def save_tenders(rows: list[dict[str, Any]], action: str = "update") -> None:
    """Write atomically, keeping a timestamped backup of the previous version."""
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if TENDERS_FILE.exists():
        shutil.copy2(TENDERS_FILE, BACKUP_DIR / f"tenders_{stamp}.json")
        _prune_backups()

    tmp = TENDERS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(TENDERS_FILE)          # atomic on the same filesystem

    write_meta({
        "lastAction": action,
        "lastSaved": datetime.now().isoformat(timespec="seconds"),
        "recordCount": len(rows),
    })


def read_meta() -> dict[str, Any]:
    if not META_FILE.exists():
        return {}
    try:
        with META_FILE.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def write_meta(patch: dict[str, Any]) -> None:
    ensure_dirs()
    meta = read_meta()
    meta.update(patch)
    with META_FILE.open("w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)


def next_id(rows: list[dict[str, Any]]) -> str:
    nums = [int("".join(filter(str.isdigit, str(r.get("id", "")))) or 0) for r in rows]
    return f"T{max(nums, default=0) + 1:03d}"


def list_backups() -> list[tuple[str, str, int]]:
    """Return (filename, readable timestamp, record count) newest first."""
    out = []
    for path in sorted(BACKUP_DIR.glob("tenders_*.json"), reverse=True):
        try:
            with path.open(encoding="utf-8") as fh:
                count = len(json.load(fh))
        except (json.JSONDecodeError, OSError):
            count = 0
        raw = path.stem.replace("tenders_", "")
        try:
            when = datetime.strptime(raw, "%Y%m%d-%H%M%S").strftime("%d %b %Y, %H:%M:%S")
        except ValueError:
            when = raw
        out.append((path.name, when, count))
    return out


def restore_backup(filename: str) -> list[dict[str, Any]]:
    """Roll the repository back to a previous save and return the restored rows."""
    path = BACKUP_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"No backup named {filename}")
    with path.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    save_tenders(rows, action=f"restored {filename}")
    return rows
