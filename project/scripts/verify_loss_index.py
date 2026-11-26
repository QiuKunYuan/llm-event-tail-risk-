"""Full private readback against raw source keys and an independent R5 reference.

Reference membership is reconstructed from past/current observations, not the
panel's declared population. It recomputes each five-market-session product,
nullable unknown reasons, history masks and all selection/calibration purges.
No row, security identifier or raw source text is printed.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import math
import time
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from build_fold_index import certified_calendar
from build_loss_index import INTERVALS, VIEW_FIELDS, check_panel_row, checked_panel_report, effective_loss_folds
from build_loss_panel import PANEL_FIELDS, count_row, spool_returns, stock_observations, summarize_counts
from loss_targets import TARGET_VERSION
from validation_io import csv_rows, fingerprints, new_private_directory, unchanged, write_json


def expected_split(origin, endpoint, fold, research_start):
    intervals = [("train", research_start, fold["train_end"], fold["selection_start"]),
                 ("selection", fold["selection_start"], fold["selection_end"], fold["calibration_start"]),
                 ("calibration", fold["calibration_start"], fold["calibration_end"], fold["test_start"]),
                 ("test", fold["test_start"], fold["test_end"], None)]
    for name, first, last, next_start in intervals:
        if first <= origin <= last:
            if next_start is not None and (not endpoint or endpoint >= next_start):
                return "purged"
            return name
    return "out_of_range"


def reference_outcome(sessions, position, observations, source_end=None):
    """Direct independent arithmetic and labels; no builder outcome function."""
    future_positions = range(position + 1, min(len(sessions), position + 6))
    available = [observations.get(sessions[i]) for i in future_positions]
    absent = sum(o is None for o in available)
    missing = sum(o is not None and o.state == "missing_return" for o in available)
    invalid = sum(o is not None and o.state not in {"valid", "missing_return"} for o in available)
    valid = [o.value for o in available if o is not None and o.state == "valid"]
    end = "" if position + 5 >= len(sessions) else sessions[position + 5]
    tail = int(not end or (source_end is not None and end > source_end))
    reasons = [reason for reason, count in (("administrative_truncation", tail),
               ("missing_future_observation", absent), ("missing_future_return", missing),
               ("invalid_future_return", invalid)) if count]
    result = dict(outcome_end=end, observed_outcome_end="", forward_5d_return="", loss_bin="",
                  label_loss="", label_loss_5pct="", label_loss_10pct="", outcome_status="unknown",
                  outcome_valid_candidate="0", outcome_certified_total_return="0",
                  supervised_candidate_mask="0", supervised_certified_mask="0",
                  future_observations_present=str(len(available) - absent), future_valid_returns=str(len(valid)),
                  missing_future_observation_count=str(absent), missing_future_return_count=str(missing),
                  invalid_future_return_count=str(invalid), administrative_truncation=str(tail), arithmetic_invalid="0",
                  unknown_reason="|".join(reasons))
    if reasons:
        return result
    # A different direct multiplication path, recomputed from source at every t.
    precision = 8 + sum(max(len(v.as_tuple().digits), 1 + abs(v.as_tuple().exponent)) for v in valid)
    with localcontext() as ctx:
        ctx.prec = max(64, precision)
        factors = [Decimal(1) + v for v in valid]
        wealth = factors[0] * factors[1] * factors[2] * factors[3] * factors[4]
        ret = wealth - Decimal(1)
    if not ret.is_finite() or not math.isfinite(float(ret)) or ret < -1:
        result.update(arithmetic_invalid="1", unknown_reason="arithmetic_invalid")
        return result
    bin_id = next((i for i, boundary in enumerate((Decimal(0), Decimal("-.05"), Decimal("-.10")))
                   if (ret >= boundary if i == 0 else ret > boundary)), 3)
    result.update(forward_5d_return=str(ret), loss_bin=str(bin_id), label_loss=str(int(ret < 0)),
                  label_loss_5pct=str(int(ret <= Decimal("-.05"))), label_loss_10pct=str(int(ret <= Decimal("-.10"))),
                  observed_outcome_end=end, outcome_valid_candidate="1", supervised_candidate_mask="1",
                  outcome_status="complete_raw_return_candidate")
    return result


def reference_candidates(con, sessions, first_dates, target_id, research_start, research_end, history_sessions, source_end=None):
    """Independent per-security timeline slicing rather than rolling builder masks."""
    for stock in sorted(first_dates):
        observations = stock_observations(con, stock)
        first = min(observations)
        presence = "0" * history_sessions + "".join("1" if d in observations else "0" for d in sessions)
        validity = "0" * history_sessions + "".join("1" if d in observations and observations[d].state == "valid" else "0" for d in sessions)
        for position, day in enumerate(sessions):
            if day < max(research_start, first) or day > research_end:
                continue
            current = observations.get(day)
            pm = presence[position + 1:position + history_sessions + 1]
            vm = validity[position + 1:position + history_sessions + 1]
            row = dict(stock_code=stock, date=day, target_version_id=target_id,
                       universe_status="OBSERVED_UNVERIFIED", universe_certified="0",
                       asof_membership_evidence="past_or_current_return_observation", asof_first_observed_date=first,
                       prediction_eligible="1", a_share_identity_certified="0", return_convention_certified="0",
                       terminal_value_certified="0", daily_tradability_known="0", daily_tradable="",
                       current_observation_present=str(int(current is not None)),
                       current_return_valid=str(int(current is not None and current.state == "valid")),
                       current_return_state=current.state if current else "absent_observation",
                       raw_return_t=current.raw if current else "", source_record_ordinal=str(current.source_ordinal) if current else "",
                       history_sessions_requested=str(history_sessions), history_observation_mask=pm,
                       history_return_valid_mask=vm, history_observation_count=str(pm.count("1")),
                       history_valid_return_count=str(vm.count("1")), history_complete=str(int(vm.count("1") == history_sessions)))
            row.update(reference_outcome(sessions, position, observations, source_end))
            yield row


def verify_rows(panel, index, sessions, folds, target_id, research_start, research_end, history_sessions, reference=None, source_end=None, view_paths=None):
    counts = {f["name"]: {} for f in folds}
    ranges = {f["name"]: {} for f in folds}
    purged_dates = {f["name"]: set() for f in folds}
    positions = {d: i for i, d in enumerate(sessions)}
    streams = [csv_rows(panel), csv_rows(index)]
    if reference is not None:
        streams.append(reference)
    view_start = len(streams)
    if view_paths is not None:
        streams.extend(csv_rows(view_paths[f["name"]]) for f in folds)
    previous = None
    n = 0
    for pair in itertools.zip_longest(*streams):
        if any(item is None for item in pair):
            raise ValueError("Raw-reference/panel/index population mismatch")
        source, row = pair[:2]
        key = check_panel_row(source, target_id, sessions, positions, history_sessions, source_end)
        if previous is not None and key <= previous:
            raise ValueError("Duplicate or unsorted actual-loss index keys")
        previous = key
        if not research_start <= row["date"] <= research_end:
            raise ValueError("Index origin outside research interval")
        if row["row_ordinal"] != str(n) or any(row[k] != source[k] for k in PANEL_FIELDS):
            raise ValueError("Index changed exact source panel keys, values or masks")
        if reference is not None and any(source[k] != pair[2][k] for k in PANEL_FIELDS):
            raise ValueError("Independent raw-source keys, membership, masks or compounded outcome mismatch")
        expected_id = hashlib.sha256((TARGET_VERSION + "\0" + target_id + "\0" + source["stock_code"] + "\0" + source["date"]).encode()).hexdigest()
        if row["index_id"] != expected_id:
            raise ValueError("Invalid immutable actual-loss index ID")
        end = row["outcome_end"]
        if row["outcome_end_ordinal"] != (str(date.fromisoformat(end).toordinal()) if end else ""):
            raise ValueError("Incorrect actual outcome-end ordinal")
        for fold_position, f in enumerate(folds):
            name = f["name"]
            split = expected_split(row["date"], end, f, research_start)
            supervised = int(row["supervised_candidate_mask"] == "1" and split in INTERVALS)
            if row["split_" + name] != split or row["supervised_candidate_" + name] != str(supervised) or row["supervised_certified_" + name] != "0":
                raise ValueError("Independent split/purge/supervision verification failed")
            if view_paths is not None:
                view = pair[view_start + fold_position]
                expected_view = {k: row[k] for k in VIEW_FIELDS if k in row}
                expected_view.update(split=split, prediction_member=row["prediction_eligible"],
                                     supervised_candidate=str(supervised), supervised_certified="0",
                                     loss_bin=row["loss_bin"] or "-1",
                                     observed_outcome_end_ordinal=str(date.fromisoformat(row["observed_outcome_end"]).toordinal())
                                     if row["observed_outcome_end"] else "")
                if view != expected_view:
                    raise ValueError("Canonical narrow fold view changed keys, masks, unknown bin or split")
            c = counts[name].setdefault(split, Counter())
            count_row(c, source)
            c["supervised_candidate_rows"] += supervised
            r = ranges[name].setdefault(split, {"first_origin": row["date"], "last_origin": row["date"], "max_outcome_end": end})
            r["first_origin"] = min(r["first_origin"], row["date"])
            r["last_origin"] = max(r["last_origin"], row["date"])
            r["max_outcome_end"] = max(r["max_outcome_end"], end)
            if split == "purged":
                purged_dates[name].add(row["date"])
        n += 1
    if not n:
        raise ValueError("Empty actual-loss index")
    return n, {name: {split: summarize_counts(c) for split, c in v.items()} for name, v in counts.items()}, ranges, purged_dates


def verify_index(returns, panel_dir, index_dir, calendar, output_dir, protocol=None):
    start = time.perf_counter()
    output = new_private_directory(output_dir)
    output.chmod(0o700)
    panel_dir, index_dir = Path(panel_dir), Path(index_dir)
    panel = panel_dir / "actual_loss_candidate_panel.csv.gz"
    index = index_dir / "actual_loss_company_day_index.csv.gz"
    report_path = index_dir / "index_report.json"
    sources = [returns, panel, index, report_path, panel_dir / "panel_report.json", calendar, Path(__file__),
               Path(__file__).with_name("loss_targets.py"), Path(__file__).with_name("build_loss_panel.py"),
               Path(__file__).with_name("build_loss_index.py"), Path(__file__).with_name("build_fold_index.py"),
               Path(__file__).with_name("point_in_time.py"), Path(__file__).with_name("validation_io.py")]
    if protocol:
        sources.append(protocol)
    before = fingerprints(sources)
    panel_report = checked_panel_report(panel_dir, before)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    identity = panel_report["target_identity_inputs"]
    if identity["returns_sha256"] != before[str(Path(returns).resolve())] or identity["calendar_sha256"] != before[str(Path(calendar).resolve())]:
        raise ValueError("Raw source/calendar hashes differ from immutable target")
    if report["index_sha256"] != before[str(index.resolve())] or report["target_version_id"] != panel_report["target_version_id"]:
        raise ValueError("Index target/SHA256 provenance mismatch")
    sessions = certified_calendar(json.loads(Path(calendar).read_text(encoding="utf-8")))
    config = json.loads(Path(protocol).read_text(encoding="utf-8")) if protocol else None
    folds = effective_loss_folds(config, sessions, panel_report["research_start"])
    view_paths = {f["name"]: index_dir / ("actual_loss_index_view_" + f["name"] + ".csv.gz") for f in folds}
    views_report_path = index_dir / "index_views_report.json"
    before.update(fingerprints([views_report_path, *view_paths.values()]))
    views_report = json.loads(views_report_path.read_text(encoding="utf-8"))
    if views_report != report["fold_views"] or views_report["parent_index_sha256"] != before[str(index.resolve())] or views_report["parent_panel_sha256"] != before[str(panel.resolve())]:
        raise ValueError("Canonical fold-view parent provenance mismatch")
    for name, path in view_paths.items():
        if views_report["views"][name] != {"file": path.name, "sha256": before[str(path.resolve())]}:
            raise ValueError("Canonical fold-view SHA256 mismatch")
    con, first_dates, source_profile = spool_returns(returns, output / "independent_source_spool.sqlite", sessions)
    try:
        reference = reference_candidates(con, sessions, first_dates, report["target_version_id"],
                                         report["research_start"], report["research_end"], panel_report["history_sessions"],
                                         source_profile["source_date_max"])
        n, counts, ranges, purged_dates = verify_rows(panel, index, sessions, folds, report["target_version_id"],
                        report["research_start"], report["research_end"], panel_report["history_sessions"], reference,
                        source_profile["source_date_max"], view_paths)
    finally:
        con.close()
    if n != report["rows"] or n != panel_report["prediction_candidates"] or n != views_report["rows_per_view"] or source_profile != panel_report["source_profile"]:
        raise ValueError("Population/source profile disagrees with builder reports")
    expected_folds = [{**f, "split_counts": counts[f["name"]], "ranges": ranges[f["name"]],
                      "purged_origin_dates": sorted(purged_dates[f["name"]])} for f in folds]
    if expected_folds != report["folds"]:
        raise ValueError("Independent fold counts/ranges/purge dates disagree with report")
    if report["input_and_code_sha256"][str(panel.resolve())] != before[str(panel.resolve())]:
        raise ValueError("Index panel-source hash mismatch")
    unchanged(before)
    result = {"scope": "full_index_readback_against_raw_exact_keys_independent_asof_masks_and_compounding",
              "rows": n, "target_version_id": report["target_version_id"], "passed": True,
              "raw_source_keys_verified": True, "asof_universe_independently_verified": True,
              "market_horizon_and_decimal_compounding_verified": True,
              "all_train_selection_calibration_test_boundaries_verified": True,
              "canonical_narrow_fold_views_verified": True,
              "unknown_outcomes_preserved": True, "fully_certified_total_return": False,
              "fully_certified_listing_universe": False, "formal_training_allowed": False,
              "folds": expected_folds, "input_and_code_sha256": before,
              "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "verification_report.json", result)
    print({"rows": n, "passed": True, "target_version_id": report["target_version_id"],
           "certification_status": "BLOCKED_OFFICIAL_SOURCES_ABSENT"}, flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--returns", type=Path, required=True)
    p.add_argument("--panel-dir", type=Path, required=True)
    p.add_argument("--index-dir", type=Path, required=True)
    p.add_argument("--calendar", type=Path, required=True)
    p.add_argument("--protocol", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    verify_index(a.returns, a.panel_dir, a.index_dir, a.calendar, a.output_dir, a.protocol)


if __name__ == "__main__":
    main()
