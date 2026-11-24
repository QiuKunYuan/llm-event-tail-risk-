"""Locked annotation membership, source integrity and human-gold preflight checks."""
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from validation_io import csv_rows, fingerprints

VERSION = "annotation-workbench-v1"
DIRECTIONS = ("改善", "恶化", "基本稳定", "混合", "不明确")
SOURCE_FIELDS = ("record_id", "family_id", "forecast_object", "stock_code", "publication_date", "report_date", "forecast_text", "proposed_split")
PACK_FILES = {
    "main": "earnings_annotations_1500_annotator_a.csv",
    "pilot": "earnings_pilot_200_annotator_a.csv",
    "double": "earnings_annotations_double_300_annotator_b.csv",
}


def unique_rows(rows):
    result = {}
    for row in rows:
        key = row.get("record_id", "")
        if not key or key in result:
            raise ValueError("Missing or duplicate record_id")
        result[key] = row
    return result


def source_matches(row, original):
    return all(row.get(k, "") == original.get(k, "") for k in SOURCE_FIELDS)


def temporal_split(publication):
    day = date.fromisoformat(publication)
    if not date(2017, 2, 6) <= day <= date(2024, 7, 24):
        raise ValueError("Publication outside the locked study window")
    return "train" if day.year <= 2019 else "validation" if day.year == 2020 else "test"


def validate_membership(main, pilot, double, sizes=(1500, 200, 300)):
    tables = [unique_rows(rows) for rows in (main, pilot, double)]
    if tuple(map(len, tables)) != sizes:
        raise ValueError("Locked annotation member counts changed")
    primary, trial, secondary = tables
    if not set(trial) <= set(primary) or not set(secondary) <= set(primary):
        raise ValueError("Pilot/double must be subsets of main")
    if any(primary[key]["proposed_split"] != "train" for key in trial):
        raise ValueError("Pilot must contain training records only")
    if any(not source_matches(row, primary[key]) for table in (trial, secondary) for key, row in table.items()):
        raise ValueError("Subset source differs from main")
    families, contents = {}, {}
    for row in main:
        split = temporal_split(row["publication_date"])
        if split != row["proposed_split"] or not row["family_id"] or not row["forecast_text"].strip():
            raise ValueError("Invalid source family, text or temporal split")
        for key, lookup in ((row["family_id"], families), (" ".join(row["forecast_text"].split()), contents)):
            if key in lookup and lookup[key] != split:
                raise ValueError("Family/content crosses temporal splits")
            lookup[key] = split
    return tables


def load_pack(directory):
    paths = {key: directory / name for key, name in PACK_FILES.items()}
    before = fingerprints(paths.values())
    rows = {key: list(csv_rows(path)) for key, path in paths.items()}
    main, pilot, double = validate_membership(rows["main"], rows["pilot"], rows["double"])
    if Counter(r["proposed_split"] for r in main.values()) != {"train": 800, "validation": 200, "test": 500}:
        raise ValueError("Locked main split counts changed")
    if len(set(pilot) & set(double)) != 42:
        raise ValueError("Locked pilot/double overlap changed")
    package_id = hashlib.sha256(json.dumps({Path(p).name: h for p, h in before.items()}, sort_keys=True).encode()).hexdigest()
    return {"main": main, "pilot": pilot, "double": double, "hashes": before, "package_id": package_id}


def selection(pack, name):
    main, pilot, double = pack["main"], pack["pilot"], pack["double"]
    keys = {
        "pilot_a": set(pilot), "pilot_b": set(pilot) & set(double),
        "remaining_a": set(main) - set(pilot), "remaining_b": set(double) - set(pilot),
        "main": set(main), "double": set(double),
    }[name]
    return [row for key, row in main.items() if key in keys]


def parse_strings(value, field):
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field}: not a JSON array") from exc
    if not isinstance(result, list) or not all(isinstance(s, str) and s.strip() for s in result):
        raise ValueError(f"{field}: must be an array of nonempty strings")
    return result


def canonical_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def label_value(row, require_review=False):
    if row.get("direction") not in DIRECTIONS:
        raise ValueError("方向未填或不是五个规定值")
    causes = parse_strings(row.get("causes_json"), "原因")
    evidence = parse_strings(row.get("evidence_json"), "证据")
    if not 1 <= len(evidence) <= 3:
        raise ValueError("证据必须为1—3段")
    body = canonical_newlines(row.get("forecast_text", ""))
    if not body.strip() or any(canonical_newlines(s) not in body for s in evidence):
        raise ValueError("证据不是正文中的逐字片段；只统一换行编码")
    if require_review:
        if (row.get("review_status") != "accepted" or row.get("needs_adjudication") != "no"
                or not row.get("reviewer", "").strip() or row.get("work_status") != "已填写"
                or row.get("annotation_version") != VERSION):
            raise ValueError("没有明确人工放行，或仍待裁决")
    return {"direction": row["direction"], "causes": causes, "evidence": evidence}


def preflight_gold(rows, originals, expected_ids=None):
    table = unique_rows(rows)
    if expected_ids is not None and set(table) != set(expected_ids):
        raise ValueError("Gold membership differs from the locked stage")
    families, contents = {}, {}
    for key, row in table.items():
        if key not in originals or not source_matches(row, originals[key]):
            raise ValueError("Gold source altered or outside locked package")
        if not row.get("family_id") or temporal_split(row["publication_date"]) != row["proposed_split"]:
            raise ValueError("Invalid family or publication boundary")
        label_value(row, require_review=True)
        for identity, lookup in ((row["family_id"], families), (" ".join(row["forecast_text"].split()), contents)):
            split = row["proposed_split"]
            if identity in lookup and lookup[identity] != split:
                raise ValueError("Gold content/family crosses splits")
            lookup[identity] = split
    return table
