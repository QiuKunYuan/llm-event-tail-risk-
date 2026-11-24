"""Shared provenance and private-output checks for CPU data acceptance tools."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import platform
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def csv_rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as source:
        yield from csv.DictReader(source)


def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return math.nan


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fingerprints(paths):
    return {str(Path(p).resolve()): sha256(p) for p in paths}


def unchanged(before):
    if before != fingerprints(before):
        raise RuntimeError("Input or code changed while validation was running")


def new_private_directory(path):
    target = Path(path).expanduser().resolve()
    if target == REPO or REPO in target.parents:
        raise ValueError("Per-record products must be outside the Git repository")
    target.mkdir(parents=True, exist_ok=False)
    return target


def environment():
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True))
    return {"python": platform.python_version(), "git_head": head, "working_tree_dirty": dirty,
            "code_identity": "file SHA256 is authoritative even before commit"}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def selected_factors(path):
    result = {}
    for row in csv_rows(path):
        if row["exchange"].strip() != "2" or row["market"].strip() != "A":
            continue
        day = row["date"]
        if day in result:
            raise ValueError("Duplicate selected-factor date")
        values = tuple(number(row[k]) for k in ("rmrf_float", "smb_float", "hml_float"))
        result[day] = values
    if not result:
        raise ValueError("Selected factor series is empty")
    return dict(sorted(result.items()))
