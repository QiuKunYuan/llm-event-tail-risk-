"""Build one private company/day index with four split columns, not four copies.

A stable index_id identifies the same labeled row for every modality and model.
Each split column has train/validation/test/purged/out_of_range; no auxiliary
availability is consulted. P5 retains the actual input spelling label_p05.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import math
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path

from point_in_time import temporal_partition
from validation_io import (csv_rows, environment, fingerprints, new_private_directory,
                           number, sha256, unchanged, write_json)


def certified_calendar(document):
    sessions = document["sessions"]
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError("Market calendar must be nonempty, sorted and unique")
    for d in sessions:
        if date.fromisoformat(d).isoformat() != d:
            raise ValueError("Calendar date is not canonical ISO format")
    if not document["audit"]["calendar_equal_in_observed_range"]:
        raise ValueError("An uncertified market calendar cannot define outcome_end")
    return sessions


def effective_folds(config, sessions, research_start):
    def first_session(d):
        i = bisect.bisect_left(sessions, d)
        if i == len(sessions):
            raise ValueError("Split starts after available market history")
        return sessions[i]
    result = []
    names = set()
    for raw in config["folds"]:
        f = dict(raw)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", f["name"]) or f["name"] in names:
            raise ValueError("Unsafe or repeated fold name")
        names.add(f["name"])
        for k in ("train_end", "validation_start", "validation_end", "test_start", "test_end"):
            if date.fromisoformat(f[k]).isoformat() != f[k]:
                raise ValueError("Noncanonical split date")
        if not (research_start <= f["train_end"] < f["validation_start"] <= f["validation_end"] < f["test_start"] <= f["test_end"]):
            raise ValueError("Invalid chronological fold boundaries")
        f["validation_start"] = first_session(f["validation_start"])
        f["test_start"] = first_session(f["test_start"])
        if f["validation_start"] > f["validation_end"] or f["test_start"] > f["test_end"]:
            raise ValueError("No observed market dates inside split")
        result.append(f)
    return result


def assignment(origin, outcome_end, fold, research_start):
    if origin < research_start or origin > fold["test_end"]:
        return "out_of_range"
    # Gaps between named intervals are not boundary purge.
    in_interval = (origin <= fold["train_end"] or
                   fold["validation_start"] <= origin <= fold["validation_end"] or
                   fold["test_start"] <= origin <= fold["test_end"])
    if not in_interval:
        return "out_of_range"
    return temporal_partition(origin, outcome_end, *(fold[k] for k in
        ("train_end", "validation_start", "validation_end", "test_start", "test_end")))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--calendar", type=Path, required=True)
    p.add_argument("--protocol", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--research-start", default="2017-01-01")
    a = p.parse_args()
    start = time.perf_counter()
    date.fromisoformat(a.research_start)
    output = new_private_directory(a.output_dir)
    sources = [a.labels, a.calendar, a.protocol, Path(__file__),
               Path(__file__).with_name("point_in_time.py"), Path(__file__).with_name("validation_io.py")]
    before = fingerprints(sources)
    calendar_doc = json.loads(a.calendar.read_text(encoding="utf-8"))
    sessions = certified_calendar(calendar_doc)
    config = json.loads(a.protocol.read_text(encoding="utf-8"))
    folds = effective_folds(config, sessions, a.research_start)
    day_cache = {}
    for i, day in enumerate(sessions[:-5]):
        end = sessions[i + 5]
        splits = [assignment(day, end, f, a.research_start) for f in folds]
        day_cache[day] = (end, date.fromisoformat(end).toordinal(), splits)
    counts = {f["name"]: Counter() for f in folds}
    positives = {f["name"]: {"p10": Counter(), "p05": Counter()} for f in folds}
    ranges = {f["name"]: {} for f in folds}
    actual_dates = {f["name"]: {} for f in folds}
    rows = 0
    previous = None
    path = output / "company_day_index.csv.gz"
    fields = ["index_id", "stock_code", "date", "outcome_end", "outcome_end_ordinal", "label_p10", "label_p05"] + ["split_" + f["name"] for f in folds]
    with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as dst:
        writer = csv.writer(dst, lineterminator="\n")
        writer.writerow(fields)
        for row in csv_rows(a.labels):
            stock, day = row["stock_code"], row["date"]
            key = stock, day
            if not re.fullmatch(r"\d{6}", stock) or (previous is not None and key <= previous):
                raise ValueError("Labels are not six-digit, uniquely stock/date sorted")
            previous = key
            if day not in day_cache:
                raise ValueError("Label origin cannot have five observed future market sessions")
            target, t10, t05 = [number(row[k]) for k in ("forward_5d_residual", "threshold_p10", "threshold_p05")]
            y10, y05 = row["label_p10"], row["label_p05"]
            if (not all(map(math.isfinite, (target, t10, t05))) or
                y10 not in {"0", "1"} or y05 not in {"0", "1"} or
                int(y10) != int(target < t10) or int(y05) != int(target < t05) or
                row["regression_window"] != "252"):
                raise ValueError("Invalid label/threshold/regression-window contract")
            end, ordinal, splits = day_cache[day]
            writer.writerow([rows, stock, day, end, ordinal, y10, y05, *splits])
            rows += 1
            for f, split in zip(folds, splits):
                name = f["name"]
                counts[name][split] += 1
                positives[name]["p10"][split] += int(y10)
                positives[name]["p05"][split] += int(y05)
                actual_dates[name].setdefault(split, set()).add(day)
                if split not in ranges[name]:
                    ranges[name][split] = {"first_origin": day, "last_origin": day, "max_outcome_end": end}
                r = ranges[name][split]
                r["first_origin"] = min(r["first_origin"], day)
                r["last_origin"] = max(r["last_origin"], day)
                r["max_outcome_end"] = max(r["max_outcome_end"], end)
    if not rows:
        raise ValueError("Empty label index")
    for f in folds:
        r = ranges[f["name"]]
        if not all(k in r for k in ("train", "validation", "test")):
            raise ValueError("Fold is missing a train/validation/test population")
        if r["train"]["max_outcome_end"] >= f["validation_start"] or r["validation"]["max_outcome_end"] >= f["test_start"]:
            raise ValueError("Outcome leakage across split boundary")
    unchanged(before)
    report = {"scope": "full_panel_shared_index_no_modality_filter", "rows": rows,
              "index_id_contract": "0-based row in this hashed index; all model modes use the same keys",
              "schema": fields, "label_mapping": {"P10": "label_p10", "P5": "label_p05"},
              "research_start": a.research_start, "protocol_version": config["version"],
              "folds": [{**f, "row_counts": dict(counts[f["name"]]),
                          "positive_counts": {k: dict(v) for k, v in positives[f["name"]].items()},
                          "ranges": ranges[f["name"]],
                          "purged_origin_dates": sorted(actual_dates[f["name"]].get("purged", set()))}
                         for f in folds],
              "sorted_unique_keys": True, "purge_checks_passed": True,
              "input_and_code_sha256": before, "index_sha256": sha256(path),
              "environment": environment(), "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "index_report.json", report)
    print({"rows": rows, "fold_counts": {k: dict(v) for k, v in counts.items()}, "purge_checks_passed": True})


if __name__ == "__main__":
    main()
