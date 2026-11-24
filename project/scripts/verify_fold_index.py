"""Read back every shared-index row and independently check labels and boundaries."""
import argparse
import bisect
import itertools
import json
from collections import Counter
from datetime import date
from pathlib import Path

from validation_io import csv_rows, fingerprints, new_private_directory, unchanged, write_json


def expected_split(origin, end, fold, start):
    if origin < start or origin > fold["test_end"]:
        return "out_of_range"
    if origin <= fold["train_end"]:
        return "train" if end < fold["validation_start"] else "purged"
    if fold["validation_start"] <= origin <= fold["validation_end"]:
        return "validation" if end < fold["test_start"] else "purged"
    if fold["test_start"] <= origin <= fold["test_end"]:
        return "test"
    return "out_of_range"


def verify(labels, index, sessions, folds, research_start):
    counts = {f["name"]: Counter() for f in folds}
    n = 0
    for n, pair in enumerate(itertools.zip_longest(csv_rows(labels), csv_rows(index)), start=1):
        original, row = pair
        if original is None or row is None:
            raise ValueError("Label/index length mismatch")
        if row["index_id"] != str(n - 1) or any(row[k] != original[k] for k in ("stock_code", "date", "label_p10", "label_p05")):
            raise ValueError("Shared index changed source keys or labels")
        i = bisect.bisect_left(sessions, row["date"])
        if i + 5 >= len(sessions) or sessions[i] != row["date"]:
            raise ValueError("Invalid origin/horizon")
        end = sessions[i + 5]
        if row["outcome_end"] != end or row["outcome_end_ordinal"] != str(date.fromisoformat(end).toordinal()):
            raise ValueError("Incorrect outcome_end or ordinal")
        for f in folds:
            expected = expected_split(row["date"], end, f, research_start)
            if row["split_" + f["name"]] != expected:
                raise ValueError("Independent split verification failed")
            counts[f["name"]][expected] += 1
    if not n:
        raise ValueError("Empty index")
    return n, {k: dict(v) for k, v in counts.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--index-dir", type=Path, required=True)
    p.add_argument("--calendar", type=Path, required=True)
    p.add_argument("--protocol", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    out = new_private_directory(a.output_dir)
    index = a.index_dir / "company_day_index.csv.gz"
    report_path = a.index_dir / "index_report.json"
    before = fingerprints([a.labels, index, report_path, a.calendar, a.protocol,
                           Path(__file__), Path(__file__).with_name("validation_io.py")])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    sessions = json.loads(a.calendar.read_text(encoding="utf-8"))["sessions"]
    protocol = json.loads(a.protocol.read_text(encoding="utf-8"))
    folds = []
    for raw in protocol["folds"]:
        f = dict(raw)
        for k in ("validation_start", "test_start"):
            f[k] = sessions[bisect.bisect_left(sessions, f[k])]
        folds.append(f)
    rows, counts = verify(a.labels, index, sessions, folds, report["research_start"])
    if rows != report["rows"] or counts != {f["name"]: f["row_counts"] for f in report["folds"]}:
        raise ValueError("Index report does not match its CSV population")
    if before[str(index.resolve())] != report["index_sha256"] or before[str(a.labels.resolve())] != report["input_and_code_sha256"][str(a.labels.resolve())]:
        raise ValueError("Index/label SHA256 does not match builder provenance")
    unchanged(before)
    result = {"scope": "full_index_readback_against_source_and_independent_boundary_conditions",
              "rows": rows, "counts": counts, "passed": True, "input_and_code_sha256": before}
    write_json(out / "verification_report.json", result)
    print({"rows": rows, "passed": True})


if __name__ == "__main__":
    main()
