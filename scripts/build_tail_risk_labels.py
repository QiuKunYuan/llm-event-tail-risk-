#!/usr/bin/env python3
"""Historical diagnostic only: reproduce the retired FF3 residual labels.

Not the actual-loss primary target; use project/scripts/build_labels.py for it.
Kept to verify immutable old evidence and its C++/independent SVD reference.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ROOT = Path("/home/admin/workspace/RESSET论文数据")
WINDOW = 252


def write_selected_factors(source: Path, target: Path) -> int:
    selected: list[tuple[str, str, str, str]] = []
    with gzip.open(source, "rt", encoding="utf-8", newline="") as src, target.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        for row in reader:
            if row["exchange"].strip() == "2" and row["market"].strip() == "A":
                selected.append(
                    (row["date"], row["rmrf_float"], row["smb_float"], row["hml_float"])
                )
        selected.sort(key=lambda x: x[0])
        if len({row[0] for row in selected}) != len(selected):
            raise ValueError("duplicate dates in selected factor series")
        writer = csv.writer(dst, lineterminator="\n")
        writer.writerows(selected)
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True,
                        help="new private historical-diagnostic output, never the primary loss panel")
    args = parser.parse_args()
    root = args.data_root
    processed = root / "processed"
    returns = processed / "daily_returns.csv.gz"
    factors = processed / "daily_factors.csv.gz"
    output = args.output or processed / "tail_risk_labels.csv.gz"
    report_path = output.with_name("tail_risk_labels_report.json")
    if output.exists() or report_path.exists():
        raise FileExistsError("Use a new output directory; existing label evidence is never overwritten")
    cpp = Path(__file__).with_name("build_tail_risk_labels.cpp")
    for path in (returns, factors, cpp):
        if not path.exists():
            raise FileNotFoundError(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch_parent = Path(os.environ.get("TMPDIR", "/tmp"))
    def digest(path: Path) -> str:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    inputs = [returns, factors, processed / "preparation_report.json", cpp, Path(__file__)]
    before_hashes = {str(p): digest(p) for p in inputs}

    with tempfile.TemporaryDirectory(prefix="tailrisk-", dir=scratch_parent) as scratch_name:
        scratch = Path(scratch_name)
        selected_factors = scratch / "selected_factors.csv"
        selected_count = write_selected_factors(factors, selected_factors)
        executable = scratch / "build_tail_risk_labels"
        subprocess.run(
            ["g++", "-O3", "-std=c++17", str(cpp), "-o", str(executable)], check=True
        )

        sorted_returns = scratch / "daily_returns_sorted.csv"
        with gzip.open(returns, "rt", encoding="utf-8", newline="") as check_header:
            if not check_header.readline().startswith("company_code,"):
                raise ValueError("daily_returns header mismatch")
        with sorted_returns.open("wb") as sorted_file:
            decompressor = subprocess.Popen(["gzip", "-dc", str(returns)], stdout=subprocess.PIPE)
            assert decompressor.stdout is not None
            header_filter = subprocess.Popen(
                ["sed", "1d"], stdin=decompressor.stdout, stdout=subprocess.PIPE
            )
            decompressor.stdout.close()
            assert header_filter.stdout is not None
            env = os.environ.copy()
            env["LC_ALL"] = "C"
            sorter = subprocess.Popen(
                [
                    "sort",
                    "-S",
                    "2G",
                    "--parallel=4",
                    f"--temporary-directory={scratch}",
                    "-t,",
                    "-k2,2",
                    "-k4,4",
                ],
                stdin=header_filter.stdout,
                stdout=sorted_file,
                env=env,
            )
            header_filter.stdout.close()
            sort_status = sorter.wait()
            filter_status = header_filter.wait()
            gzip_status = decompressor.wait()
            if sort_status or filter_status or gzip_status:
                raise subprocess.CalledProcessError(
                    sort_status or filter_status or gzip_status, "sort pipeline"
                )

        raw_labels = scratch / "tail_risk_labels.csv"
        print("Sorted input; estimating rolling factor models and labels.", flush=True)
        processing_result = subprocess.run(
            [str(executable), str(sorted_returns), str(selected_factors), str(raw_labels)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(processing_result.stderr, end="", flush=True)
        process_stats = {
            key: int(value)
            for token in processing_result.stderr.split()
            if "=" in token
            for key, value in [token.split("=", 1)]
        }
        expected_rows = json.loads(
            (processed / "preparation_report.json").read_text(encoding="utf-8")
        )["tables"]["daily_returns"]["rows_written"]
        if process_stats.get("lines") != expected_rows or process_stats.get("rows") != expected_rows:
            raise ValueError(
                f"DRET row count mismatch: expected {expected_rows}, got {process_stats}"
            )
        if process_stats.get("malformed") or process_stats.get("unmapped"):
            raise ValueError(f"DRET rows failed parsing/alignment: {process_stats}")

        counts: Counter[str] = Counter()
        min_date = max_date = None
        min_stock = max_stock = None
        first_key = previous_key = None
        positive10 = positive05 = 0
        with raw_labels.open("r", encoding="utf-8", newline="") as src, gzip.open(
            output, "wt", encoding="utf-8", newline="", compresslevel=6
        ) as dst:
            reader = csv.DictReader(src)
            writer = csv.DictWriter(dst, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                key = (row["stock_code"], row["date"])
                if first_key is None:
                    first_key = key
                if previous_key is not None and key <= previous_key:
                    raise ValueError(f"labels are not uniquely sorted: {previous_key} -> {key}")
                previous_key = key
                writer.writerow(row)
                counts["rows"] += 1
                positive10 += int(row["label_p10"])
                positive05 += int(row["label_p05"])
                d, s = row["date"], row["stock_code"]
                min_date = d if min_date is None else min(min_date, d)
                max_date = d if max_date is None else max(max_date, d)
                min_stock = s if min_stock is None else min(min_stock, s)
                max_stock = s if max_stock is None else max(max_stock, s)

    if before_hashes != {str(p): digest(p) for p in inputs}:
        raise RuntimeError("Input or generator changed during label rebuilding")
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input_and_code_sha256": before_hashes,
        "output_sha256": digest(output),
        "input_returns": str(returns),
        "input_factors": str(factors),
        "output": str(output),
        "factor_filter": {"exchange": "2", "market": "A", "weighting": "float value weighted"},
        "baseline_features": {
            "as_of": "prediction date close",
            "fields": [
                "return_1d",
                "cumulative_return_5d",
                "cumulative_return_20d",
                "volatility_20d",
                "volatility_60d",
                "downside_volatility_60d",
                "valid_returns_20d",
                "valid_returns_60d",
                "rmrf_float",
                "smb_float",
                "hml_float",
            ],
            "volatility": "sample standard deviation of available excess returns; require at least 80% window coverage",
            "cumulative_returns": "compound raw stock returns; only emitted when every market session in the window is present",
        },
        "factor_regression": {
            "dependent_variable": "individual daily return minus daily risk-free rate",
            "regressors": ["intercept", "Rmrf_float", "Smb_float", "Hml_float"],
            "window": "last 252 observations with finite stock return, risk-free rate and all three factors through prediction date",
            "minimum_observations": WINDOW,
        },
        "label": {
            "horizon": "next 5 market trading days",
            "outcome": "sum of frozen-beta daily factor residuals",
            "history": "252 most recent valid prior outcomes completed by the prediction date",
            "quantile": "fixed 252 outcomes: order statistics 26 (P10) and 13 (P05); strict less-than threshold",
            "p10_and_p05": "p05 included for robustness",
            "purge": "outcome_end must be strictly before the next split start",
        },
        "selected_factor_rows": selected_count,
        "input_rows": process_stats["rows"],
        "aligned_rows": process_stats["rows"],
        "valid_forward_targets_before_history_warmup": process_stats["targets"],
        "label_rows": counts["rows"],
        "positive_p10": positive10,
        "positive_p10_rate": positive10 / counts["rows"] if counts["rows"] else None,
        "positive_p05": positive05,
        "positive_p05_rate": positive05 / counts["rows"] if counts["rows"] else None,
        "date_range": {"min": min_date, "max": max_date},
        "stock_code_range": {"min": min_stock, "max": max_stock},
        "sorted_unique_by_stock_date": True,
        "input_rows_missing_return_excluded_from_estimation_and_horizon": True,
    }
    temp_report = report_path.with_suffix(".json.tmp")
    temp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_report.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
