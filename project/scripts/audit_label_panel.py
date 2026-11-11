#!/usr/bin/env python3
"""Audit the existing tail-label panel against its output and split contracts."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from pathlib import Path


DEFAULT_DATA_ROOT = Path("/home/admin/workspace/RESSET论文数据")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "reports" / "label_panel_audit.json",
    )
    args = parser.parse_args()
    root = args.data_root.expanduser().resolve()
    processed = root / "processed"
    labels_path = processed / "tail_risk_labels.csv.gz"
    label_report_path = processed / "tail_risk_labels_report.json"
    baseline_report_path = processed / "baseline_comparison" / "run_report.json"
    for path in (labels_path, label_report_path, baseline_report_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    source_label_report = json.loads(label_report_path.read_text(encoding="utf-8"))
    baseline_report = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    required = {
        "stock_code", "date", "forward_5d_residual", "threshold_p10", "label_p10",
        "threshold_p05", "label_p05", "regression_window",
    }
    rows = 0
    counts: Counter[str] = Counter()
    labels_by_year: Counter[str] = Counter()
    feature_missing: Counter[str] = Counter()
    dates: set[str] = set()
    first_key: tuple[str, str] | None = None
    previous_key: tuple[str, str] | None = None
    order_violations = 0
    duplicate_keys = 0
    p10_mismatches = 0
    p05_mismatches = 0
    invalid_labels = 0
    missing_required: list[str] = []

    with gzip.open(labels_path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        headers = reader.fieldnames or []
        missing_required = sorted(required - set(headers))
        if missing_required:
            raise ValueError(f"Label panel missing required columns: {missing_required}")

        for row in reader:
            rows += 1
            stock = row["stock_code"].strip()
            day = row["date"].strip()
            key = (stock, day)
            dates.add(day)
            if first_key is None:
                first_key = key
            if previous_key is not None:
                if key < previous_key:
                    order_violations += 1
                if key == previous_key:
                    duplicate_keys += 1
            previous_key = key

            try:
                target = float(row["forward_5d_residual"])
                threshold10 = float(row["threshold_p10"])
                threshold05 = float(row["threshold_p05"])
                y10 = int(row["label_p10"])
                y05 = int(row["label_p05"])
                regression_window = int(float(row["regression_window"]))
            except (TypeError, ValueError):
                invalid_labels += 1
                continue
            if y10 not in (0, 1) or y05 not in (0, 1):
                invalid_labels += 1
            if y10 != int(target < threshold10):
                p10_mismatches += 1
            if y05 != int(target < threshold05):
                p05_mismatches += 1
            if regression_window != 252:
                counts["non_252_regression_window"] += 1
            counts["p10_positive"] += y10
            counts["p05_positive"] += y05
            labels_by_year[day[:4]] += 1

            for field in (
                "return_1d", "cumulative_return_5d", "cumulative_return_20d",
                "volatility_20d", "volatility_60d", "downside_volatility_60d",
                "rmrf_float", "smb_float", "hml_float",
            ):
                if not row.get(field, "").strip():
                    feature_missing[field] += 1

    sorted_dates = sorted(dates)
    date_position = {day: i for i, day in enumerate(sorted_dates)}
    split_checks = []
    seen_years = set()
    for fold in baseline_report.get("folds", []):
        year = fold["test_year"]
        if year in seen_years:
            continue
        seen_years.add(year)
        first_test = fold["test_first_date"]
        last_train = fold["train_last_date"]
        if first_test not in date_position or last_train not in date_position:
            gap = None
            passes = False
        else:
            gap = date_position[first_test] - date_position[last_train] - 1
            # Five full market sessions between the last training origin and first test origin.
            passes = gap == 5
        split_checks.append({
            "test_year": year,
            "train_last_date": last_train,
            "first_test_date": first_test,
            "excluded_market_sessions": gap,
            "five_session_purge_confirmed": passes,
            "reported_test_rows": fold["test_rows"],
            "panel_rows_in_test_year": labels_by_year[str(year)],
        })

    baseline_features = baseline_report.get("features", [])
    forbidden_feature_tokens = ("forward", "threshold", "label", "target")
    contaminated_features = [
        feature for feature in baseline_features
        if any(token in feature.lower() for token in forbidden_feature_tokens)
    ]

    result = {
        "source_files": {
            "labels": str(labels_path),
            "label_report": str(label_report_path),
            "baseline_report": str(baseline_report_path),
        },
        "panel": {
            "rows": rows,
            "date_min": sorted_dates[0] if sorted_dates else None,
            "date_max": sorted_dates[-1] if sorted_dates else None,
            "distinct_market_dates": len(sorted_dates),
            "rows_by_year": dict(sorted(labels_by_year.items())),
            "duplicate_stock_date_keys": duplicate_keys,
            "sort_order_violations": order_violations,
            "invalid_label_rows": invalid_labels,
            "p10_label_vs_strict_threshold_mismatches": p10_mismatches,
            "p05_label_vs_strict_threshold_mismatches": p05_mismatches,
            "non_252_regression_window_rows": counts["non_252_regression_window"],
            "p10_positive_rows": counts["p10_positive"],
            "p10_positive_rate": counts["p10_positive"] / rows if rows else None,
            "p05_positive_rows": counts["p05_positive"],
            "p05_positive_rate": counts["p05_positive"] / rows if rows else None,
            "baseline_feature_missing_counts": dict(feature_missing),
        },
        "label_report_consistency": {
            "rows_match": rows == source_label_report.get("label_rows"),
            "date_min_match": sorted_dates[0] == source_label_report.get("date_range", {}).get("min") if sorted_dates else False,
            "date_max_match": sorted_dates[-1] == source_label_report.get("date_range", {}).get("max") if sorted_dates else False,
            "p10_positive_count_match": counts["p10_positive"] == source_label_report.get("positive_p10"),
            "p05_positive_count_match": counts["p05_positive"] == source_label_report.get("positive_p05"),
        },
        "baseline_contract": {
            "features": baseline_features,
            "future_or_label_fields_in_feature_list": contaminated_features,
            "purge_sessions_requested": baseline_report.get("purge_trading_days"),
            "fold_checks": split_checks,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
