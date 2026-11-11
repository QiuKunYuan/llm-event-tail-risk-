#!/usr/bin/env python3
"""Stream-audit normalized RESSET inputs used by the thesis project."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from datetime import date
from pathlib import Path


DEFAULT_DATA_ROOT = Path("/home/admin/workspace/RESSET论文数据")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

TABLES = {
    "daily_returns": {
        "file": "daily_returns.csv.gz",
        "dates": ["date"],
        "required": ["stock_code", "date", "return", "risk_free_rate"],
    },
    "daily_factors": {
        "file": "daily_factors.csv.gz",
        "dates": ["date"],
        "required": [
            "date", "exchange", "market", "rmrf_float", "smb_float", "hml_float",
            "rmrf_total", "smb_total", "hml_total",
        ],
    },
    "industry_daily": {
        "file": "industry_daily.csv.gz",
        "dates": ["date"],
        "required": ["date", "industry_sector", "return_float"],
    },
    "earnings_events": {
        "file": "earnings_events.csv.gz",
        "dates": ["publication_date", "report_date"],
        "required": ["stock_code", "publication_date", "forecast_text"],
    },
    "lawsuit_events": {
        "file": "lawsuit_events.csv.gz",
        "dates": ["first_publication_date", "publication_date"],
        "required": ["stock_code", "publication_date", "behavior_text"],
    },
    "shareholders": {
        "file": "shareholders.csv.gz",
        "dates": ["report_date", "publication_date"],
        "required": [
            "stock_code", "report_date", "publication_date", "holder_rank",
            "holder_name", "holding_shares", "pct_total",
        ],
    },
    "industry_history": {
        "file": "industry_history.csv.gz",
        "dates": ["publication_date", "effective_date", "cancel_date"],
        "required": ["stock_code", "publication_date", "industry_sector"],
    },
    "status_history": {
        "file": "status_history.csv.gz",
        "dates": ["change_date"],
        "required": ["stock_code", "change_date", "change_type"],
    },
}


def parse_date(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    try:
        if len(value) == 8 and value.isdigit():
            return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def packed_stock_date(stock_code: str, day: str) -> int | tuple[str, str] | None:
    if not stock_code or not day:
        return None
    try:
        # Date ordinals are below 1,000,000 for all supported project dates.
        return int(stock_code) * 1_000_000 + date.fromisoformat(day).toordinal()
    except ValueError:
        return (stock_code, day)


def audit_table(name: str, definition: dict, processed: Path) -> dict:
    path = processed / definition["file"]
    if not path.is_file():
        return {"file": str(path), "error": "missing"}

    rows = 0
    missing: Counter[str] = Counter()
    all_missing: Counter[str] = Counter()
    date_ranges = {field: {"min": None, "max": None, "invalid": 0} for field in definition["dates"]}
    key_seen: set[int | tuple[str, str]] | None = set() if name == "daily_returns" else None
    duplicate_count = 0
    duplicate_examples: list[dict[str, str]] = []
    code_rows = 0
    non_six_digit_stock_codes = 0
    unusual_stock_code_examples: set[str] = set()
    factor_groups: Counter[str] = Counter()
    factor_dates: dict[str, set[str]] = {}
    duplicate_factor_keys = 0
    shareholder_counts: Counter[str] = Counter()

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames or []
        missing_columns = [field for field in definition["required"] if field not in headers]
        for row in reader:
            rows += 1
            for field in headers:
                if not row.get(field, "").strip():
                    all_missing[field] += 1
            for field in definition["required"]:
                if not row.get(field, "").strip():
                    missing[field] += 1

            for field in definition["dates"]:
                raw = row.get(field, "")
                normalized = parse_date(raw)
                if raw.strip() and normalized is None:
                    date_ranges[field]["invalid"] += 1
                if normalized:
                    lo = date_ranges[field]["min"]
                    hi = date_ranges[field]["max"]
                    date_ranges[field]["min"] = normalized if lo is None else min(lo, normalized)
                    date_ranges[field]["max"] = normalized if hi is None else max(hi, normalized)

            stock_code = row.get("stock_code", "").strip()
            if stock_code:
                code_rows += 1
                if len(stock_code) != 6 or not stock_code.isdigit():
                    non_six_digit_stock_codes += 1
                    if len(unusual_stock_code_examples) < 20:
                        unusual_stock_code_examples.add(stock_code)

            if key_seen is not None:
                day = parse_date(row.get("date", ""))
                key = packed_stock_date(stock_code, day or "")
                if key is not None:
                    if key in key_seen:
                        duplicate_count += 1
                        if len(duplicate_examples) < 20:
                            duplicate_examples.append({"stock_code": stock_code, "date": day or ""})
                    else:
                        key_seen.add(key)

            if name == "daily_factors":
                group = f"{row.get('exchange', '').strip()}|{row.get('market', '').strip()}"
                factor_groups[group] += 1
                seen_dates = factor_dates.setdefault(group, set())
                day = parse_date(row.get("date", ""))
                if day:
                    if day in seen_dates:
                        duplicate_factor_keys += 1
                    seen_dates.add(day)

            if name == "shareholders":
                rank_raw = row.get("holder_rank", "").strip()
                pct_float = row.get("pct_float", "").strip()
                holder_id = row.get("holder_id", "").strip()
                try:
                    top_ten = float(rank_raw) <= 10
                except ValueError:
                    top_ten = False
                if pct_float:
                    shareholder_counts["pct_float_present"] += 1
                if top_ten:
                    shareholder_counts["rank_top_10"] += 1
                if top_ten and pct_float:
                    shareholder_counts["rank_top_10_and_pct_float_present"] += 1
                    nature = row.get("holder_nature", "").strip()
                    if nature == "自然人":
                        shareholder_counts["candidate_natural_person"] += 1
                    else:
                        shareholder_counts["candidate_non_natural_person"] += 1
                    if holder_id:
                        shareholder_counts["candidate_with_holder_id"] += 1
                        if nature != "自然人":
                            shareholder_counts["non_natural_candidate_with_holder_id"] += 1
                    else:
                        shareholder_counts["candidate_missing_holder_id"] += 1
                        if nature != "自然人":
                            shareholder_counts["non_natural_candidate_missing_holder_id"] += 1

    result = {
        "file": str(path),
        "rows": rows,
        "headers": headers,
        "missing_required_columns": missing_columns,
        "required_field_missing_counts": dict(missing),
        "all_field_missing_counts": dict(all_missing),
        "date_ranges": date_ranges,
        "stock_code_rows": code_rows,
        "non_six_digit_stock_code_rows": non_six_digit_stock_codes,
        "non_six_digit_stock_code_examples": sorted(unusual_stock_code_examples),
    }
    if key_seen is not None:
        result["stock_date_key"] = {
            "unique_keys": len(key_seen),
            "duplicate_rows": duplicate_count,
            "duplicate_examples": duplicate_examples,
        }
    if name == "daily_factors":
        result["factor_group_rows"] = dict(sorted(factor_groups.items()))
        result["factor_group_unique_dates"] = {
            group: len(days) for group, days in sorted(factor_dates.items())
        }
        result["duplicate_factor_group_date_rows"] = duplicate_factor_keys
    if name == "shareholders":
        result["holder_graph_candidate_counts"] = dict(shareholder_counts)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "reports" / "input_quality.json",
    )
    args = parser.parse_args()
    root = args.data_root.expanduser().resolve()
    processed = root / "processed"
    tables = {
        name: audit_table(name, definition, processed)
        for name, definition in TABLES.items()
    }
    result = {
        "data_root": str(root),
        "audit_scope": "stream scan of normalized RESSET CSV.GZ files",
        "tables": tables,
        "interpretation": {
            "daily_returns_duplicate_key": "stock_code + date",
            "factor_duplicate_key": "exchange + market + date",
            "shareholder_candidate_rule": "holder_rank <= 10 and pct_float is present; holder_id is audited but not required for the count",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
