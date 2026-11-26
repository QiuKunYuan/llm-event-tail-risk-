"""Build one immutable actual-loss candidate index with four leakage-purged folds.

All observed-unverified prediction candidates, including unknown outcomes and
incomplete history, retain their keys. Fold prediction membership is separate
from candidate supervision and never grants total-return certification.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import re
import time
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from build_fold_index import certified_calendar
from build_loss_panel import PANEL_FIELDS, count_row, summarize_counts
from loss_targets import (NULL_OUTCOME_FIELDS, TARGET_CONTRACT, TARGET_VERSION, UNIVERSE_STATUS,
                          approved_folds, canonical_day, labels_from_return, parse_return,
                          source_key, stable_index_id)
from validation_io import (csv_rows, environment, fingerprints, new_private_directory,
                           sha256, unchanged, write_json)

INTERVALS = ("train", "selection", "calibration", "test")
VIEW_FIELDS = ["index_id", "row_ordinal", "stock_code", "date", "split", "prediction_member",
               "supervised_candidate", "supervised_certified", "loss_bin", "label_loss",
               "label_loss_5pct", "label_loss_10pct", "outcome_status", "unknown_reason",
               "outcome_end_ordinal", "observed_outcome_end_ordinal", "target_version_id", "universe_status"]


def narrow_view(row, fold_name):
    """A target-bound canonical adapter view; -1 is unknown, never a safe bin."""
    view = {k: row[k] for k in VIEW_FIELDS if k in row}
    view.update(split=row["split_" + fold_name], prediction_member=row["prediction_eligible"],
                supervised_candidate=row["supervised_candidate_" + fold_name],
                supervised_certified=row["supervised_certified_" + fold_name],
                loss_bin=row["loss_bin"] if row["loss_bin"] != "" else -1,
                observed_outcome_end_ordinal=date.fromisoformat(row["observed_outcome_end"]).toordinal()
                if row["observed_outcome_end"] else "")
    return view


def export_fold_views(index, output, folds, parent_sha256, panel_sha256, target_id):
    """Stream four narrow adapter views, not four copies of feature histories."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        writers = {}
        paths = {}
        n = 0
        for f in folds:
            name = f["name"]
            path = output / ("actual_loss_index_view_" + name + ".csv.gz")
            dst = stack.enter_context(gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6))
            writer = csv.DictWriter(dst, fieldnames=VIEW_FIELDS, lineterminator="\n")
            writer.writeheader()
            writers[name], paths[name] = writer, path
        for row in csv_rows(index):
            for name, writer in writers.items():
                writer.writerow(narrow_view(row, name))
            n += 1
    report = {"target_version_id": target_id, "parent_index_sha256": parent_sha256,
              "parent_panel_sha256": panel_sha256, "rows_per_view": n, "schema": VIEW_FIELDS,
              "split_names": list(INTERVALS) + ["purged", "out_of_range"],
              "unknown_loss_bin": -1, "prediction_member_is_observed_unverified": True,
              "formal_training_allowed": False,
              "views": {name: {"file": path.name, "sha256": sha256(path)} for name, path in paths.items()}}
    write_json(output / "index_views_report.json", report)
    return report


def effective_loss_folds(config, sessions, research_start):
    raw_folds = approved_folds() if config is None else config["folds"]
    if not raw_folds:
        raise ValueError("Empty loss-fold protocol")
    result = []
    names = set()
    for raw in raw_folds:
        f = dict(raw)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", f.get("name", "")) or f["name"] in names:
            raise ValueError("Unsafe or repeated loss-fold name")
        names.add(f["name"])
        keys = ("train_end", "selection_start", "selection_end", "calibration_start",
                "calibration_end", "test_start", "test_end")
        if not all(k in f for k in keys):
            raise ValueError("Actual-loss folds require independent selection AND calibration intervals")
        for k in keys:
            canonical_day(f[k])
        if not (research_start <= f["train_end"] < f["selection_start"] <= f["selection_end"] <
                f["calibration_start"] <= f["calibration_end"] < f["test_start"] <= f["test_end"]):
            raise ValueError("Invalid chronological actual-loss intervals")
        for k in ("selection_start", "calibration_start", "test_start"):
            i = bisect.bisect_left(sessions, f[k])
            if i == len(sessions):
                raise ValueError("Loss split begins after available market calendar")
            f[k] = sessions[i]
            if f[k] > f[k.replace("start", "end")]:
                raise ValueError("Loss split has no observed market session")
        result.append(f)
    return result


def loss_assignment(origin, outcome_end, fold, research_start):
    if origin < research_start or origin > fold["test_end"]:
        return "out_of_range"
    if origin <= fold["train_end"]:
        return "train" if outcome_end and outcome_end < fold["selection_start"] else "purged"
    for interval, next_start in (("selection", "calibration_start"), ("calibration", "test_start")):
        if fold[interval + "_start"] <= origin <= fold[interval + "_end"]:
            return interval if outcome_end and outcome_end < fold[next_start] else "purged"
    if fold["test_start"] <= origin <= fold["test_end"]:
        return "test"
    return "out_of_range"


def check_panel_row(row, target_id, sessions, positions, history_sessions, source_end=None):
    """Check shape and economic semantics without filtering prediction membership."""
    if any(k not in row for k in PANEL_FIELDS):
        raise ValueError("Actual-loss panel schema is incomplete")
    stock, day = source_key(row["stock_code"], row["date"])
    if day not in positions or row["target_version_id"] != target_id:
        raise ValueError("Wrong panel market day or target identity")
    if row["universe_status"] != UNIVERSE_STATUS or row["prediction_eligible"] != "1":
        raise ValueError("Not an observed-unverified prediction candidate")
    for flag in ("universe_certified", "a_share_identity_certified", "return_convention_certified",
                 "terminal_value_certified", "daily_tradability_known", "outcome_certified_total_return", "supervised_certified_mask"):
        if row[flag] != "0":
            raise ValueError("Candidate implementation cannot assert absent source certification")
    if row["daily_tradable"] != "" or row["asof_membership_evidence"] != "past_or_current_return_observation":
        raise ValueError("Unsupported tradability or as-of membership evidence")
    canonical_day(row["asof_first_observed_date"])
    if row["asof_first_observed_date"] > day:
        raise ValueError("Future-leaking membership evidence")
    for k in ("current_observation_present", "current_return_valid", "history_complete", "outcome_valid_candidate",
              "supervised_candidate_mask", "administrative_truncation", "arithmetic_invalid"):
        if row[k] not in {"0", "1"}:
            raise ValueError("Invalid binary candidate mask")
    present = int(row["current_observation_present"])
    valid_current = int(row["current_return_valid"])
    if present:
        observation = parse_return(row["raw_return_t"])
        if row["current_return_state"] != observation.state or valid_current != int(observation.state == "valid"):
            raise ValueError("Current raw return disagrees with its validity mask")
        if not row["source_record_ordinal"].isdigit() or int(row["source_record_ordinal"]) < 1:
            raise ValueError("Present row lacks exact source-record ordinal")
    elif row["raw_return_t"] or row["source_record_ordinal"] or row["current_return_state"] != "absent_observation" or valid_current:
        raise ValueError("Absent source row cannot be synthesized or marked valid")
    masks = [row["history_observation_mask"], row["history_return_valid_mask"]]
    if row["history_sessions_requested"] != str(history_sessions) or any(len(m) != history_sessions or set(m) - {"0", "1"} for m in masks):
        raise ValueError("Invalid historical availability masks")
    if any(a == "0" and b == "1" for a, b in zip(*masks)) or masks[0][-1] != str(present) or masks[1][-1] != str(valid_current):
        raise ValueError("Historical validity/observation masks are inconsistent")
    if row["history_observation_count"] != str(masks[0].count("1")) or row["history_valid_return_count"] != str(masks[1].count("1")) or row["history_complete"] != str(int(masks[1].count("1") == history_sessions)):
        raise ValueError("Incorrect history count/completeness")
    i = positions[day]
    end = sessions[i + 5] if i + 5 < len(sessions) else ""
    administrative = int(not end or (source_end is not None and end > source_end))
    if row["outcome_end"] != end or row["administrative_truncation"] != str(administrative):
        raise ValueError("Incorrect market-session endpoint/administrative truncation")
    known = row["outcome_valid_candidate"] == "1"
    if row["supervised_candidate_mask"] != str(int(known)):
        raise ValueError("Candidate supervision must be distinct from certification and history")
    if known:
        try:
            value = Decimal(row["forward_5d_return"])
            expected = labels_from_return(value)
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Invalid finite economic loss target") from exc
        if not end or row["observed_outcome_end"] != end or row["unknown_reason"] or row["outcome_status"] != "complete_raw_return_candidate":
            raise ValueError("Incorrect complete candidate outcome state")
        if any(row[k] != str(v) for k, v in expected.items()):
            raise ValueError("Incorrect loss-bin/equality label semantics")
        if any(row[k] != "0" for k in ("missing_future_observation_count", "missing_future_return_count", "invalid_future_return_count", "arithmetic_invalid")) or row["future_valid_returns"] != "5" or row["future_observations_present"] != "5":
            raise ValueError("Complete outcome lacks five valid market-session returns")
    elif any(row[k] for k in NULL_OUTCOME_FIELDS) or row["observed_outcome_end"] or not row["unknown_reason"] or row["outcome_status"] != "unknown":
        raise ValueError("Unknown outcome must remain nullable, not safe")
    return stock, day


def checked_panel_report(panel_dir, before):
    report = json.loads((Path(panel_dir) / "panel_report.json").read_text(encoding="utf-8"))
    panel = Path(panel_dir) / "actual_loss_candidate_panel.csv.gz"
    if report.get("target_version") != TARGET_VERSION or report.get("target_contract") != TARGET_CONTRACT:
        raise ValueError("Legacy/different target panel cannot reuse actual-loss IDs")
    if report["panel_sha256"] != before[str(panel.resolve())]:
        raise ValueError("Candidate panel SHA256 does not match provenance")
    payload = report["target_identity_inputs"]
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if identity != report["target_version_id"] or payload["target_contract"] != TARGET_CONTRACT:
        raise ValueError("Wrong immutable target identity")
    return report


def build_index(panel_dir, calendar, output_dir, protocol=None):
    start = time.perf_counter()
    output = new_private_directory(output_dir)
    output.chmod(0o700)
    panel_dir = Path(panel_dir)
    panel = panel_dir / "actual_loss_candidate_panel.csv.gz"
    sources = [panel, panel_dir / "panel_report.json", calendar, Path(__file__),
               Path(__file__).with_name("loss_targets.py"), Path(__file__).with_name("build_loss_panel.py"),
               Path(__file__).with_name("validation_io.py"), Path(__file__).with_name("build_fold_index.py"),
               Path(__file__).with_name("point_in_time.py")]
    if protocol:
        sources.append(protocol)
    before = fingerprints(sources)
    panel_report = checked_panel_report(panel_dir, before)
    if panel_report["target_identity_inputs"]["calendar_sha256"] != before[str(Path(calendar).resolve())]:
        raise ValueError("Index calendar differs from target identity")
    sessions = certified_calendar(json.loads(Path(calendar).read_text(encoding="utf-8")))
    positions = {d: i for i, d in enumerate(sessions)}
    config = json.loads(Path(protocol).read_text(encoding="utf-8")) if protocol else None
    folds = effective_loss_folds(config, sessions, panel_report["research_start"])
    schema = ["index_id", "row_ordinal", *PANEL_FIELDS, "outcome_end_ordinal"]
    for f in folds:
        schema += ["split_" + f["name"], "supervised_candidate_" + f["name"], "supervised_certified_" + f["name"]]
    fold_counts = {f["name"]: {} for f in folds}
    dates = {f["name"]: {} for f in folds}
    ranges = {f["name"]: {} for f in folds}
    previous = None
    n = 0
    index = output / "actual_loss_company_day_index.csv.gz"
    target_id = panel_report["target_version_id"]
    with gzip.open(index, "wt", encoding="utf-8", newline="", compresslevel=6) as dst:
        writer = csv.DictWriter(dst, fieldnames=schema, lineterminator="\n")
        writer.writeheader()
        for row in csv_rows(panel):
            key = check_panel_row(row, target_id, sessions, positions, panel_report["history_sessions"],
                                  panel_report["source_profile"]["source_date_max"])
            if previous is not None and key <= previous:
                raise ValueError("Candidate panel keys are not sorted and unique")
            if not panel_report["research_start"] <= row["date"] <= panel_report["research_end"]:
                raise ValueError("Panel origin outside research interval")
            previous = key
            end = row["outcome_end"]
            out = {**row, "index_id": stable_index_id(target_id, *key), "row_ordinal": n,
                   "outcome_end_ordinal": date.fromisoformat(end).toordinal() if end else ""}
            for f in folds:
                name = f["name"]
                split = loss_assignment(row["date"], end, f, panel_report["research_start"])
                supervised = int(row["supervised_candidate_mask"] == "1" and split in INTERVALS)
                out.update({"split_" + name: split, "supervised_candidate_" + name: supervised,
                            "supervised_certified_" + name: 0})
                c = fold_counts[name].setdefault(split, Counter())
                count_row(c, row)
                c["supervised_candidate_rows"] += supervised
                dates[name].setdefault(split, set()).add(row["date"])
                r = ranges[name].setdefault(split, {"first_origin": row["date"], "last_origin": row["date"], "max_outcome_end": end})
                r["first_origin"] = min(r["first_origin"], row["date"])
                r["last_origin"] = max(r["last_origin"], row["date"])
                r["max_outcome_end"] = max(r["max_outcome_end"], end)
            writer.writerow(out)
            n += 1
    if not n or n != panel_report["prediction_candidates"]:
        raise ValueError("Candidate panel/index population mismatch")
    for f in folds:
        r = ranges[f["name"]]
        if not all(k in r for k in INTERVALS):
            raise ValueError("Fold lacks train/selection/calibration/test prediction candidates")
        for split, boundary in (("train", "selection_start"), ("selection", "calibration_start"), ("calibration", "test_start")):
            if r[split]["max_outcome_end"] >= f[boundary]:
                raise ValueError("Outcome leakage across actual-loss boundary")
    index_hash = sha256(index)
    views = export_fold_views(index, output, folds, index_hash, panel_report["panel_sha256"], target_id)
    unchanged(before)
    report = {"scope": "full_observed_unverified_prediction_index_no_future_or_modality_filter",
              "target_version": TARGET_VERSION, "target_version_id": target_id,
              "index_id_contract": "SHA256(target_version+NUL+target_identity+NUL+exact_stock_code+NUL+date)",
              "row_ordinal_contract": "zero_based_row_in_this_hashed_index_not_legacy_residual_ID",
              "target_contract": TARGET_CONTRACT, "rows": n, "schema": schema,
              "research_start": panel_report["research_start"], "research_end": panel_report["research_end"],
              "protocol_version": config.get("version") if config else TARGET_VERSION + "-approved-four-folds",
              "folds": [{**f, "split_counts": {k: summarize_counts(v) for k, v in fold_counts[f["name"]].items()},
                         "ranges": ranges[f["name"]], "purged_origin_dates": sorted(dates[f["name"]].get("purged", set()))} for f in folds],
              "sorted_unique_keys": True, "purge_checks_passed": True,
              "certification_status": "BLOCKED_OFFICIAL_SOURCES_ABSENT", "formal_training_allowed": False,
              "input_and_code_sha256": before, "index_sha256": index_hash, "fold_views": views,
              "environment": environment(), "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "index_report.json", report)
    print({"rows": n, "index_sha256": report["index_sha256"], "purge_checks_passed": True,
           "fold_counts": {name: {split: c["prediction_candidates"] for split, c in v.items()} for name, v in fold_counts.items()},
           "certification_status": report["certification_status"]}, flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--panel-dir", type=Path, required=True)
    p.add_argument("--calendar", type=Path, required=True)
    p.add_argument("--protocol", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    build_index(a.panel_dir, a.calendar, a.output_dir, a.protocol)


if __name__ == "__main__":
    main()
