#!/usr/bin/env python3
"""Create a reproducible, stratified manual-annotation pack from earnings events."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


DEFAULT_DATA_ROOT = Path("/home/admin/workspace/RESSET论文数据")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_FIELDS = [
    "record_id", "family_id", "forecast_object", "stock_code", "publication_date", "report_date", "forecast_text",
    "proposed_split", "direction", "causes_json", "evidence_json",
    "annotator_confidence", "needs_adjudication", "annotator_note",
]


def event_id(row: dict[str, str]) -> str:
    identity = "\x1f".join(
        row.get(field, "").strip()
        for field in ("stock_code", "publication_date", "report_date", "forecast_object", "forecast_type", "forecast_text")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def proposed_split(publication_date: str) -> str:
    year = int(publication_date[:4])
    if year <= 2019:
        return "train"
    if year == 2020:
        return "validation"
    return "test"


def allocate_quotas(groups: dict[tuple[str, str], list[dict[str, str]]], total: int) -> dict[tuple[str, str], int]:
    keys = sorted(groups)
    quotas = {key: 0 for key in keys}
    if total >= len(keys):
        for key in keys:
            quotas[key] = 1
    remaining = total - sum(quotas.values())
    while remaining > 0:
        capacity = {key: len(groups[key]) - quotas[key] for key in keys if len(groups[key]) > quotas[key]}
        if not capacity:
            break
        capacity_total = sum(capacity.values())
        exact = {key: remaining * value / capacity_total for key, value in capacity.items()}
        allocated = 0
        for key, share in exact.items():
            amount = min(capacity[key], int(share))
            quotas[key] += amount
            allocated += amount
        remaining -= allocated
        if remaining <= 0:
            break
        ranked = sorted(
            capacity,
            key=lambda key: (exact[key] - int(exact[key]), capacity[key], key),
            reverse=True,
        )
        for key in ranked:
            if remaining == 0:
                break
            if quotas[key] < len(groups[key]):
                quotas[key] += 1
                remaining -= 1
        if allocated == 0 and not ranked:
            break
    return quotas


def stratified_sample(rows: list[dict[str, str]], count: int, seed: int) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["publication_date"][:4], row.get("forecast_type", ""))].append(row)
    quotas = allocate_quotas(groups, min(count, len(rows)))
    rng = random.Random(seed)
    selected = []
    for key in sorted(groups):
        selected.extend(rng.sample(groups[key], quotas[key]))
    selected.sort(key=lambda row: (row["publication_date"], row["stock_code"], row["record_id"]))
    return selected


def write_template(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=ANNOTATION_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in ANNOTATION_FIELDS})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--start-date", default="2017-02-06")
    parser.add_argument("--end-date", default="2024-07-24")
    parser.add_argument("--sample-size", type=int, default=1500)
    parser.add_argument("--double-size", type=int, default=300)
    parser.add_argument("--pilot-size", type=int, default=200)
    parser.add_argument("--train-size", type=int, default=800)
    parser.add_argument("--validation-size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "outputs" / "annotations",
    )
    args = parser.parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must not precede --start-date")
    source_path = args.data_root.expanduser().resolve() / "processed" / "earnings_events.csv.gz"
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    rows: list[dict[str, str]] = []
    exclusions: Counter[str] = Counter()
    seen_ids: set[str] = set()
    with gzip.open(source_path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        for source_row in reader:
            publication = source_row.get("publication_date", "").strip()
            text = source_row.get("forecast_text", "").strip()
            stock_code = source_row.get("stock_code", "").strip()
            if not publication:
                exclusions["missing_publication_date"] += 1
                continue
            try:
                publication_day = date.fromisoformat(publication[:10])
            except ValueError:
                exclusions["invalid_publication_date"] += 1
                continue
            if publication_day < start or publication_day > end:
                exclusions["outside_study_window"] += 1
                continue
            if not stock_code:
                exclusions["missing_stock_code"] += 1
                continue
            if not text:
                exclusions["missing_forecast_text"] += 1
                continue
            row = {
                "stock_code": stock_code,
                "publication_date": publication_day.isoformat(),
                "report_date": source_row.get("report_date", "").strip(),
                "forecast_type": source_row.get("forecast_type", "").strip(),
                "forecast_object": source_row.get("forecast_object", "").strip(),
                "forecast_text": text,
            }
            row["record_id"] = event_id(row)
            family = "\x1f".join(row[k] for k in ("stock_code", "report_date", "forecast_object"))
            row["family_id"] = hashlib.sha256(family.encode()).hexdigest()[:20]
            if row["record_id"] in seen_ids:
                exclusions["exact_duplicate_event"] += 1
                continue
            seen_ids.add(row["record_id"])
            row["proposed_split"] = proposed_split(row["publication_date"])
            rows.append(row)

    family_splits: dict[str, set[str]] = {}
    for row in rows:
        family_splits.setdefault(row["family_id"], set()).add(row["proposed_split"])
    conflicts = {f for f, splits in family_splits.items() if len(splits) > 1}
    exclusions["cross_split_event_family"] = sum(row["family_id"] in conflicts for row in rows)
    rows = [row for row in rows if row["family_id"] not in conflicts]
    if len(rows) < args.sample_size:
        raise ValueError(f"Only {len(rows)} eligible events; requested {args.sample_size}")
    quotas = {"train": args.train_size, "validation": args.validation_size,
              "test": args.sample_size - args.train_size - args.validation_size}
    if any(v < 1 for v in quotas.values()):
        raise ValueError("Positive train/validation/test annotation quotas required")
    sample = []
    for offset, (split, count) in enumerate(quotas.items()):
        pool = [r for r in rows if r["proposed_split"] == split]
        if len(pool) < count:
            raise ValueError("Insufficient eligible events in " + split)
        sample.extend(stratified_sample(pool, count, args.seed + offset * 10))
    sample.sort(key=lambda r: (r["publication_date"], r["stock_code"], r["record_id"]))
    training = [r for r in sample if r["proposed_split"] == "train"]
    if args.pilot_size < 0 or args.pilot_size > len(training):
        raise ValueError("Pilot must fit inside the main sample's training-year subset")
    if args.double_size < 0 or args.double_size > len(sample):
        raise ValueError("Double annotation size must fit inside the main sample")
    pilot = stratified_sample(training, args.pilot_size, args.seed + 2)
    double_sample = stratified_sample(sample, args.double_size, args.seed + 1)
    primary_path = args.output_dir / f"earnings_annotations_{len(sample)}_annotator_a.csv"
    double_path = args.output_dir / f"earnings_annotations_double_{len(double_sample)}_annotator_b.csv"
    pilot_path = args.output_dir / f"earnings_pilot_{len(pilot)}_annotator_a.csv"
    for path in [primary_path, double_path, pilot_path, args.output_dir / "sampling_report.json"]:
        if path.exists():
            raise FileExistsError("Refusing to overwrite an existing annotation pack: " + str(path))
    write_template(primary_path, sample)
    write_template(double_path, double_sample)
    write_template(pilot_path, pilot)

    sample_distribution = Counter(
        f"{row['publication_date'][:4]}|type={row['forecast_type']}" for row in sample
    )
    report = {
        "source": str(source_path),
        "study_window": {"start": start.isoformat(), "end": end.isoformat()},
        "eligible_unique_events": len(rows),
        "sample_size": len(sample),
        "double_annotation_size": len(double_sample),
        "pilot_size": len(pilot),
        "pilot_is_training_subset_of_main_sample": True,
        "pilot_template": str(pilot_path),
        "seed": args.seed,
        "exclusions": dict(exclusions),
        "sample_rows_by_year_and_source_forecast_type": dict(sorted(sample_distribution.items())),
        "split_rows": dict(Counter(row["proposed_split"] for row in sample)),
        "primary_template": str(primary_path),
        "double_annotation_template": str(double_path),
        "annotation_schema": "direction, causes_json, evidence_json; outputs are intentionally blank",
    }
    report_path = args.output_dir / "sampling_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
