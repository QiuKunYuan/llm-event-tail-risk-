"""Stream an OBSERVED UNVERIFIED as-of universe and nullable raw-R5 outcomes.

The SQLite spool and all row-level products are NEW private artifacts outside
Git. At most one security's market-calendar history is held in memory. Earlier
residual products are neither read as labels nor overwritten. No future factor,
risk-free rate, 252-day regression or outcome warmup is required.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import sqlite3
import time
from collections import Counter, deque
from pathlib import Path

from build_fold_index import certified_calendar
from loss_targets import (TARGET_CONTRACT, TARGET_VERSION, UNIVERSE_STATUS, ReturnObservation,
                          canonical_day, coverage_bounds, outcome_at, parse_return,
                          source_key, target_identity)
from validation_io import (csv_rows, environment, fingerprints, new_private_directory,
                           sha256, unchanged, write_json)

PANEL_FIELDS = [
    "stock_code", "date", "target_version_id", "universe_status", "universe_certified",
    "asof_membership_evidence", "asof_first_observed_date", "prediction_eligible",
    "a_share_identity_certified", "return_convention_certified", "terminal_value_certified",
    "daily_tradability_known", "daily_tradable", "current_observation_present", "current_return_valid",
    "current_return_state", "raw_return_t", "source_record_ordinal", "history_sessions_requested",
    "history_observation_mask", "history_return_valid_mask", "history_observation_count",
    "history_valid_return_count", "history_complete", "outcome_end", "observed_outcome_end",
    "forward_5d_return", "loss_bin", "label_loss", "label_loss_5pct", "label_loss_10pct", "outcome_status",
    "outcome_valid_candidate", "outcome_certified_total_return", "supervised_candidate_mask",
    "supervised_certified_mask", "future_observations_present", "future_valid_returns",
    "missing_future_observation_count", "missing_future_return_count", "invalid_future_return_count",
    "administrative_truncation", "arithmetic_invalid", "unknown_reason",
]
GAPS = ["official_Dret_Daret_units_dividend_adjustment_and_suspension_valuation_dictionary",
        "complete_historical_security_master_A_share_listing_delisting_and_code_identity_intervals",
        "daily_trading_suspension_resumption_and_valuation_status",
        "terminal_delisting_cash_conversion_and_recovery_values"]


def spool_returns(path, database, sessions):
    """Retain exact stock/date/return spellings and 1-based source-record ordinal.

    CSV input need not be stock-sorted. Duplicate keys are fatal, not aggregated.
    The existing source streaming helper is the only CSV decoding path.
    """
    con = sqlite3.connect(database)
    con.execute("PRAGMA cache_size=-65536")
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("CREATE TABLE returns (stock TEXT NOT NULL, day TEXT NOT NULL, raw TEXT NOT NULL, "
                "state TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(stock,day)) WITHOUT ROWID")
    calendar_set = set(sessions)
    dates = set()
    first_dates = {}
    states = Counter()
    batch = []
    count = 0
    try:
        for count, row in enumerate(csv_rows(path), 1):
            if not {"stock_code", "date", "return"}.issubset(row):
                raise ValueError("Source is missing required raw-return columns")
            stock, day = source_key(row["stock_code"], row["date"])
            if day not in calendar_set:
                raise ValueError("A raw-return source date is absent from the certified market calendar")
            observation = parse_return(row["return"], count)
            states[observation.state] += 1
            dates.add(day)
            first_dates[stock] = min(day, first_dates.get(stock, day))
            batch.append((stock, day, observation.raw, observation.state, count))
            if len(batch) == 25000:
                con.executemany("INSERT INTO returns VALUES (?,?,?,?,?)", batch)
                con.commit()
                batch.clear()
        if batch:
            con.executemany("INSERT INTO returns VALUES (?,?,?,?,?)", batch)
            con.commit()
        if not count:
            raise ValueError("Empty raw-return source")
        # A global missing source day must not shorten the independent market
        # calendar. Keep its rows nullable and report the source-coverage gap.
        expected = {d for d in sessions if min(dates) <= d <= max(dates)}
    except sqlite3.IntegrityError as exc:
        con.close()
        raise ValueError("Duplicate exact stock/date source keys") from exc
    except Exception:
        con.close()
        raise
    return con, first_dates, {"rows": count, "securities": len(first_dates), "return_states": dict(states),
                              "source_date_min": min(dates), "source_date_max": max(dates),
                              "source_market_sessions": len(dates), "source_calendar_range_equal": dates == expected,
                              "global_market_sessions_missing_in_source": len(expected - dates),
                              "exact_source_keys_unique": True}


def stock_observations(con, stock):
    return {day: parse_return(raw, ordinal)
            for day, raw, ordinal in con.execute("SELECT day,raw,ordinal FROM returns WHERE stock=? ORDER BY day", (stock,))}


def candidate_rows(sessions, stock, observations, target_id, research_start, research_end, history_sessions=60, source_end=None):
    """Membership uses only evidence at/before t, never a future last-seen date.

    Disappeared securities remain observed-unverified candidates. This is not a
    certified claim that they remain listed. Missing current rows and short
    history retain their masks and cannot be dropped by future outcome coverage.
    """
    if not observations:
        return
    first = min(observations)
    present_history = deque([0] * history_sessions, maxlen=history_sessions)
    valid_history = deque([0] * history_sessions, maxlen=history_sessions)
    present_count = valid_count = 0
    for i, day in enumerate(sessions):
        current = observations.get(day)
        present = int(current is not None)
        valid = int(current is not None and current.state == "valid")
        present_count += present - present_history[0]
        valid_count += valid - valid_history[0]
        present_history.append(present)
        valid_history.append(valid)
        if day > research_end:
            break
        if day < research_start or day < first:
            continue
        row = dict(stock_code=stock, date=day, target_version_id=target_id,
                   universe_status=UNIVERSE_STATUS, universe_certified=0,
                   asof_membership_evidence="past_or_current_return_observation",
                   asof_first_observed_date=first, prediction_eligible=1,
                   a_share_identity_certified=0, return_convention_certified=0, terminal_value_certified=0,
                   daily_tradability_known=0, daily_tradable="", current_observation_present=present,
                   current_return_valid=valid, current_return_state=current.state if current else "absent_observation",
                   raw_return_t=current.raw if current else "", source_record_ordinal=current.source_ordinal if current else "",
                   history_sessions_requested=history_sessions,
                   history_observation_mask="".join(map(str, present_history)),
                   history_return_valid_mask="".join(map(str, valid_history)),
                   history_observation_count=present_count, history_valid_return_count=valid_count,
                   history_complete=int(valid_count == history_sessions))
        row.update(outcome_at(sessions, i, observations, source_end))
        yield row


def summarize_counts(counts):
    n = counts["prediction_candidates"]
    unknown = counts["unknown_outcomes"]
    return {**dict(counts), "candidate_coverage": {event: coverage_bounds(n, counts[event], unknown)
            for event in ("label_loss", "label_loss_5pct", "label_loss_10pct")},
            "fully_certified_outcomes": 0, "fully_certified_universe": False}


def count_row(counts, row):
    counts["prediction_candidates"] += 1
    valid = int(row["outcome_valid_candidate"])
    counts["complete_raw_return_candidate_outcomes"] += valid
    counts["unknown_outcomes"] += 1 - valid
    counts["missing_current_observation"] += 1 - int(row["current_observation_present"])
    counts["invalid_or_missing_current_return"] += 1 - int(row["current_return_valid"])
    counts["incomplete_history"] += 1 - int(row["history_complete"])
    for reason in str(row["unknown_reason"]).split("|"):
        if reason:
            counts["unknown_reason_" + reason] += 1
    if valid:
        counts["loss_bin_" + str(row["loss_bin"])] += 1
        for event in ("label_loss", "label_loss_5pct", "label_loss_10pct"):
            counts[event] += int(row[event])


def build_panel(returns, calendar, output_dir, research_start="2017-01-01", research_end="2024-07-24", history_sessions=60):
    start = time.perf_counter()
    canonical_day(research_start)
    canonical_day(research_end)
    if research_start > research_end or not isinstance(history_sessions, int) or history_sessions < 1:
        raise ValueError("Invalid research interval/history length")
    output = new_private_directory(output_dir)
    output.chmod(0o700)
    sources = [returns, calendar, Path(__file__), Path(__file__).with_name("loss_targets.py"),
               Path(__file__).with_name("validation_io.py"), Path(__file__).with_name("build_fold_index.py"),
               Path(__file__).with_name("point_in_time.py")]
    before = fingerprints(sources)
    calendar_doc = json.loads(Path(calendar).read_text(encoding="utf-8"))
    sessions = certified_calendar(calendar_doc)
    if not sessions[0] <= research_start <= research_end <= sessions[-1]:
        raise ValueError("Research interval is not inside observed market calendar")
    actual_start = sessions[bisect.bisect_left(sessions, research_start)]
    target_id, identity = target_identity(before[str(Path(returns).resolve())], before[str(Path(calendar).resolve())],
                                          research_start, research_end, history_sessions)
    con, first_dates, source_profile = spool_returns(returns, output / "raw_return_spool.sqlite", sessions)
    counts = Counter()
    daily = {}
    panel = output / "actual_loss_candidate_panel.csv.gz"
    try:
        with gzip.open(panel, "wt", encoding="utf-8", newline="", compresslevel=6) as dst:
            writer = csv.DictWriter(dst, fieldnames=PANEL_FIELDS, lineterminator="\n")
            writer.writeheader()
            for stock in sorted(first_dates):
                observations = stock_observations(con, stock)
                for row in candidate_rows(sessions, stock, observations, target_id, research_start, research_end, history_sessions,
                                          source_profile["source_date_max"]):
                    writer.writerow(row)
                    count_row(counts, row)
                    count_row(daily.setdefault(row["date"], Counter()), row)
    finally:
        con.close()
    if not counts["prediction_candidates"]:
        raise ValueError("Empty observed-unverified candidate population")
    unchanged(before)
    report = {"scope": "full_observed_unverified_candidate_universe_not_certified_A_share_census",
              "target_version": TARGET_VERSION, "target_version_id": target_id,
              "target_contract": TARGET_CONTRACT, "target_identity_inputs": identity,
              "research_start": research_start, "actual_first_research_market_session": actual_start,
              "research_end": research_end, "history_sessions": history_sessions,
              "schema": PANEL_FIELDS, "source_profile": source_profile, **summarize_counts(counts),
              "membership_depends_on_future_outcome": False, "membership_uses_future_last_observation": False,
              "prediction_eligibility_requires_complete_history": False,
              "supervised_candidate_is_not_certified_mask": True,
              "calendar_certification_scope": "existing_DRET_factor_session_crosscheck_not_listing_or_return_certification",
              "certification_status": "BLOCKED_OFFICIAL_SOURCES_ABSENT", "required_missing_evidence": GAPS,
              "formal_training_allowed": False, "input_and_code_sha256": before,
              "panel_sha256": sha256(panel), "sqlite_spool_sha256": sha256(output / "raw_return_spool.sqlite"),
              "environment": environment(), "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "panel_report.json", report)
    write_json(output / "daily_candidate_coverage.json", {"universe_status": UNIVERSE_STATUS,
               "certified_census": False, "daily": {day: summarize_counts(c) for day, c in sorted(daily.items())}})
    print({"prediction_candidates": counts["prediction_candidates"],
           "complete_candidate_outcomes": counts["complete_raw_return_candidate_outcomes"],
           "unknown_outcomes": counts["unknown_outcomes"], "target_version_id": target_id,
           "panel_sha256": report["panel_sha256"], "certification_status": report["certification_status"]}, flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--returns", type=Path, required=True)
    p.add_argument("--calendar", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--research-start", default="2017-01-01")
    p.add_argument("--research-end", default="2024-07-24")
    p.add_argument("--history-sessions", type=int, default=60)
    a = p.parse_args()
    build_panel(a.returns, a.calendar, a.output_dir, a.research_start, a.research_end, a.history_sessions)


if __name__ == "__main__":
    main()
