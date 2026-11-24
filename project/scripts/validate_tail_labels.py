"""Independently reconstruct sampled labels with NumPy SVD least squares.

Profiles the full DRET calendar before selecting stocks without future risk labels.
Detailed stock/day comparisons and the certified calendar remain outside Git.
This is sampled independent validation, not an independent full-panel algorithm.
"""
from __future__ import annotations

import argparse
import math
import time
from collections import Counter, deque
from pathlib import Path

import numpy as np
from validation_io import (csv_rows, environment, fingerprints, new_private_directory,
                           number, selected_factors, unchanged, write_json)

WINDOW = 252
HORIZON = 5
FIELDS = ("forward_5d_residual", "threshold_p10", "threshold_p05")
ATOL = 1e-9
RTOL = 1e-8


def reference_labels(days, factors, rows):
    """Refit each origin from raw design rows; never update normal equations."""
    positions = {d: i for i, d in enumerate(days)}
    x = np.column_stack((np.ones(len(days)), np.asarray([factors[d] for d in days])))
    ret = np.full(len(days), np.nan)
    rf = ret.copy()
    present = np.zeros(len(days), dtype=bool)
    for row in rows:
        d = positions[row["date"]]
        if present[d]:
            raise ValueError("Duplicate stock/date in independent reference input")
        present[d] = True
        ret[d], rf[d] = number(row["return"]), number(row["risk_free_rate"])
    finite = np.isfinite(ret) & np.isfinite(rf) & np.isfinite(x).all(1)
    origins = np.flatnonzero(present)
    observations = deque(maxlen=WINDOW)
    targets = {}
    regressions = rank_failures = incomplete = 0
    for d in origins:
        if finite[d]:
            observations.append(d)
        if len(observations) != WINDOW:
            continue
        indices = list(observations)
        beta, _, rank, _ = np.linalg.lstsq(x[indices], (ret - rf)[indices], rcond=None)
        regressions += 1
        if rank != 4:
            rank_failures += 1
            continue
        end = d + HORIZON
        if end >= len(days) or not finite[d + 1:end + 1].all():
            incomplete += 1
            continue
        future = slice(d + 1, end + 1)
        targets[d] = float(np.sum(ret[future] - rf[future] - x[future] @ beta))
    history = deque(maxlen=WINDOW)
    completed = sorted(targets)
    cursor = 0
    output = {}
    boundary_counts = Counter()
    for d in origins:
        while cursor < len(completed) and completed[cursor] + HORIZON <= d:
            history.append(targets[completed[cursor]])
            cursor += 1
        if d not in targets or len(history) != WINDOW:
            continue
        ordered = np.sort(np.asarray(history))
        t10, t05 = float(ordered[25]), float(ordered[12])
        target = targets[d]
        output[days[d]] = {"forward_5d_residual": target, "threshold_p10": t10,
                           "threshold_p05": t05, "label_p10": int(target < t10),
                           "label_p05": int(target < t05)}
        if not finite[d]:
            boundary_counts["labels_with_missing_origin_observation"] += 1
        if days[d][:4] != days[d + HORIZON][:4]:
            boundary_counts["forward_windows_crossing_year"] += 1
        if d >= 60 and not finite[d - 59:d + 1].all():
            boundary_counts["labels_after_recent_missing_or_absent_observation"] += 1
    return output, {"regressions": regressions, "rank_deficient_regressions": rank_failures,
                    "incomplete_future_windows_rejected": incomplete, "label_rows": len(output),
                    "boundary_counts": dict(boundary_counts)}


def profile_returns(path, factors):
    stats = {}
    dates = set()
    rows = invalid = 0
    for row in csv_rows(path):
        rows += 1
        day, stock = row["date"], row["stock_code"]
        dates.add(day)
        finite = (math.isfinite(number(row["return"])) and
                  math.isfinite(number(row["risk_free_rate"])) and
                  all(map(math.isfinite, factors.get(day, (math.nan,)))))
        s = stats.setdefault(stock, {"rows": 0, "valid": 0, "missing": 0, "min": day, "max": day})
        s["rows"] += 1
        s["valid"] += finite
        s["missing"] += not finite
        s["min"], s["max"] = min(s["min"], day), max(s["max"], day)
        invalid += not finite
    if not dates:
        raise ValueError("DRET calendar is empty")
    calendar = sorted(dates)
    expected = [d for d in factors if calendar[0] <= d <= calendar[-1]]
    report = {"dret_rows": rows, "stocks": len(stats), "finite_estimation_rows": rows - invalid,
              "invalid_estimation_rows": invalid, "sessions": len(calendar),
              "date_min": calendar[0], "date_max": calendar[-1],
              "selected_factor_sessions": len(factors),
              "dret_dates_without_selected_factors": len(dates - set(factors)),
              "factor_dates_absent_from_dret_inside_observed_range": len(set(expected) - dates),
              "calendar_equal_in_observed_range": calendar == expected}
    if not report["calendar_equal_in_observed_range"]:
        raise ValueError("Factor calendar differs from the observed full-market DRET calendar")
    return stats, calendar, report


def select_stocks(stats, calendar, count=20, seed=20261124):
    """Cover short histories, missing values and absent days, then a seeded spread."""
    pos = {d: i for i, d in enumerate(calendar)}
    eligible = [s for s, v in sorted(stats.items()) if s and v["valid"] >= 600]
    if count < 20 or len(eligible) < count:
        raise ValueError("At least 20 eligible stocks are required")
    selected = []
    reasons = {}
    def take(ordered, quota, reason):
        added = 0
        for s in ordered:
            if s in reasons:
                continue
            selected.append(s)
            reasons[s] = reason
            added += 1
            if added == quota:
                break
    take(sorted(eligible, key=lambda s: (stats[s]["valid"], s)), 4, "short_eligible_history")
    take(sorted(eligible, key=lambda s: (-stats[s]["missing"], s)), 4, "many_missing_observations")
    take(sorted(eligible, key=lambda s: (-(pos[stats[s]["max"]] - pos[stats[s]["min"]] + 1 - stats[s]["rows"]), s)), 4, "many_absent_market_days")
    rng = np.random.default_rng(seed)
    take([eligible[i] for i in rng.permutation(len(eligible))], count - len(selected), "seeded_spread")
    return selected, reasons


def compare_panel(path, references):
    observed = {s: set() for s in references}
    counts = Counter()
    max_errors = {field: 0.0 for field in FIELDS}
    for row in csv_rows(path):
        stock, day = row["stock_code"], row["date"]
        if stock not in references:
            continue
        counts["sample_panel_rows"] += 1
        if day in observed[stock]:
            counts["duplicate_sample_keys"] += 1
        observed[stock].add(day)
        expected = references[stock].get(day)
        if expected is None:
            counts["panel_only_keys"] += 1
            continue
        for field in FIELDS:
            value = number(row[field])
            error = abs(value - expected[field])
            if math.isfinite(error):
                max_errors[field] = max(max_errors[field], error)
            if not math.isfinite(value) or not np.isclose(value, expected[field], atol=ATOL, rtol=RTOL):
                counts[field + "_mismatches"] += 1
        for field in ("label_p10", "label_p05"):
            if row[field] != str(expected[field]):
                counts[field + "_mismatches"] += 1
    counts["reference_only_keys"] = sum(len(set(v) - observed[s]) for s, v in references.items())
    for k in ("duplicate_sample_keys", "panel_only_keys", "reference_only_keys", *(f + "_mismatches" for f in FIELDS), "label_p10_mismatches", "label_p05_mismatches"):
        counts.setdefault(k, 0)
    passed = all(v == 0 for k, v in counts.items() if k != "sample_panel_rows")
    return {"counts": dict(counts), "max_absolute_errors": max_errors, "passed": passed}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--returns", type=Path, required=True)
    p.add_argument("--factors", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--stocks", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261124)
    a = p.parse_args()
    start = time.perf_counter()
    output = new_private_directory(a.output_dir)
    sources = [a.returns, a.factors, a.labels, Path(__file__), Path(__file__).with_name("validation_io.py")]
    before = fingerprints(sources)
    factors = selected_factors(a.factors)
    stats, calendar, calendar_report = profile_returns(a.returns, factors)
    selected, reasons = select_stocks(stats, calendar, a.stocks, a.seed)
    sampled = {s: [] for s in selected}
    for row in csv_rows(a.returns):
        if row["stock_code"] in sampled:
            sampled[row["stock_code"]].append(row)
    # Use the DRET calendar, independently certified equal to factors inside its range.
    references, detail, aggregate = {}, {}, Counter()
    for stock, rows in sampled.items():
        ref, metrics = reference_labels(calendar, factors, rows)
        references[stock] = ref
        detail[stock] = {"selection_reason": reasons[stock], "input_profile": stats[stock], **metrics}
        for k, v in metrics.items():
            if k != "boundary_counts":
                aggregate[k] += v
        aggregate.update(metrics["boundary_counts"])
    comparison = compare_panel(a.labels, references)
    unchanged(before)
    provenance = {"file_sha256": before, "environment": environment(), "numpy": np.__version__}
    write_json(output / "market_calendar.json", {"scope": "DRET full-market dates", "sessions": calendar,
                                                "audit": calendar_report, "provenance": provenance})
    write_json(output / "sample_details.json", {"stocks": detail, "reference_labels": references})
    report = {"scope": "independent_SVD_reference_on_sample_not_independent_full_panel",
              "sample_stocks": len(selected), "selection_seed": a.seed,
              "selection_uses_future_labels": False, "calendar": calendar_report,
              "reference": dict(aggregate), "comparison": comparison,
              "tolerance": {"absolute": ATOL, "relative": RTOL},
              "provenance": provenance, "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "validation_report.json", report)
    print({"sample_stocks": len(selected), "reference": dict(aggregate), "comparison": comparison}, flush=True)
    if not comparison["passed"] or not aggregate["label_rows"]:
        raise RuntimeError("Independent label acceptance failed; inspect the private report")


if __name__ == "__main__":
    main()
