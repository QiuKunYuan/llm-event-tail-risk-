"""Build LIMITED real observed-unverified price windows; validation/forward CPU only.

Inputs are corrected DRET CSV, audited market calendar and COMPLETED actual-loss
panel/index/view reports. Explicit dates/stocks are selected before any outcome
inspection. Stream the source once into a small private SQLite window cache,
retaining exact raw spellings/source record ordinals; never use a future stock
observation, latest industry, rmrf, or an invented market proxy. Source repeated
market-return values are audited across all securities for each requested history
date. Default market context remains masked because identity/units are unverified;
--market-candidate-context explicitly permits only date-consistent raw candidate
values, still NOT certified market returns. No actual risk training or financial
scores are emitted: optional CPU forward uses untrained weights for shape checks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import torch
from build_fold_index import certified_calendar
from loss_targets import TARGET_CONTRACT, canonical_day, parse_return, source_key
from research_model import DisclosureModel
from risk_batch_contract import RiskBatchContract, ROLES, SCHEMA, SPLITS
from validation_io import csv_rows, fingerprints, new_private_directory, sha256, unchanged, write_json


def checked_sources(returns, calendar, panel_report, index_report, views_report, index_view, fold):
    """Require final report/hash chain, never consume an index still being written."""
    paths = [Path(p).resolve() for p in (returns, calendar, panel_report, index_report, views_report, index_view)]
    returns, calendar, panel_report_path, index_report_path, views_report_path, index_view = paths
    panel = panel_report_path.parent / "actual_loss_candidate_panel.csv.gz"
    parent_index = index_report_path.parent / "actual_loss_company_day_index.csv.gz"
    before = fingerprints([*paths, panel, parent_index])
    pr, ir, vr = [json.loads(p.read_text(encoding="utf-8")) for p in (panel_report_path, index_report_path, views_report_path)]
    payload = pr["target_identity_inputs"]
    target_id = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (pr.get("target_contract") != TARGET_CONTRACT or target_id != pr.get("target_version_id") or
            payload.get("returns_sha256") != before[str(returns)] or payload.get("calendar_sha256") != before[str(calendar)]):
        raise ValueError("Panel target/source/calendar provenance mismatch")
    if pr.get("formal_training_allowed") is not False or pr.get("fully_certified_universe") is not False:
        raise ValueError("Adapter accepts observed-unverified candidates only")
    if pr.get("panel_sha256") != before[str(panel)] or vr.get("parent_panel_sha256") != before[str(panel)]:
        raise ValueError("Panel/view source SHA mismatch")
    if (ir.get("index_sha256") != before[str(parent_index)] or vr.get("parent_index_sha256") != before[str(parent_index)] or
            ir.get("target_version_id") != target_id or vr.get("target_version_id") != target_id or
            ir.get("sorted_unique_keys") is not True or ir.get("purge_checks_passed") is not True or
            ir.get("formal_training_allowed") is not False or vr.get("formal_training_allowed") is not False):
        raise ValueError("Completed immutable index/view report chain is not valid")
    view = vr["views"].get(str(fold))
    if not view or view["file"] != index_view.name or view["sha256"] != before[str(index_view)]:
        raise ValueError("Requested narrow fold view SHA/name mismatch")
    folds = [f for f in ir["folds"] if f["name"] == str(fold)]
    if len(folds) != 1:
        raise ValueError("Fold is absent from completed actual-loss index")
    sessions = certified_calendar(json.loads(calendar.read_text(encoding="utf-8")))
    return before, pr, folds[0], sessions, panel, parent_index


def select_index(index_view, days, stocks, target_id):
    """Stream the full source view; selection never depends on known future labels."""
    selected, previous, first_roles = {}, None, {}
    days, stocks = set(days), set(stocks)
    for row in csv_rows(index_view):
        stock, day = source_key(row["stock_code"], row["date"])
        key = stock, day
        if previous is not None and key <= previous:
            raise ValueError("Narrow index is not uniquely stock/date sorted")
        previous = key
        if row["target_version_id"] != target_id or row["split"] not in {*SPLITS, "purged", "out_of_range"}:
            raise ValueError("Index target/role mismatch")
        if row["split"] in SPLITS:
            first_roles[row["split"]] = min(day, first_roles.get(row["split"], day))
        if stock not in stocks or day not in days:
            continue
        if row["prediction_member"] != "1" or row["universe_status"] != "OBSERVED_UNVERIFIED" or row["supervised_certified"] != "0":
            raise ValueError("Selected row is not an uncertified prediction candidate")
        if row["split"] not in SPLITS:
            raise ValueError("Explicit date/stock has purged/out_of_range role; choose an active index date")
        selected[(day, stock)] = row
    expected = {(day, stock) for day in days for stock in stocks}
    if set(selected) != expected:
        raise ValueError("Explicit stock/date is absent from the as-of prediction universe; not silently dropped")
    if not all(role in first_roles for role in SPLITS):
        raise ValueError("Source fold does not contain all chronological roles")
    return selected, {role: first_roles[nxt] for role, nxt in zip(SPLITS, SPLITS[1:])}


def stream_window_cache(returns, database, sessions, needed_days, stocks, market_column):
    """Read every source row for date consistency, store only selected past windows."""
    session_set, needed, stocks = set(sessions), set(needed_days), set(stocks)
    con = sqlite3.connect(database)
    con.execute("PRAGMA cache_size=-8192")
    con.execute("CREATE TABLE prices(stock TEXT, day TEXT, raw TEXT, state TEXT, source_record INTEGER, PRIMARY KEY(stock,day)) WITHOUT ROWID")
    market = {day: {"value": None, "raw": "", "first_source_record": 0, "rows": 0, "missing": 0, "invalid": 0, "conflicting": 0} for day in needed}
    dates, counts = set(), Counter()
    try:
        for ordinal, row in enumerate(csv_rows(returns), 1):
            if not {"stock_code", "date", "return", market_column} <= row.keys():
                raise ValueError("Corrected DRET source is missing explicit price/market columns")
            day = row["date"]
            if day not in session_set:
                raise ValueError("Source return date is not in the audited market calendar")
            dates.add(day); counts["source_rows"] += 1
            if day not in needed:
                continue
            m = market[day]; m["rows"] += 1
            observation = parse_return(row[market_column], ordinal)
            if observation.state == "missing_return":
                m["missing"] += 1
            elif observation.state != "valid":
                m["invalid"] += 1
            elif m["value"] is None:
                m.update(value=observation.value, raw=observation.raw, first_source_record=ordinal)
            elif observation.value != m["value"]:
                m["conflicting"] += 1
            if row["stock_code"] in stocks:
                stock, _ = source_key(row["stock_code"], day)
                value = parse_return(row["return"], ordinal)
                try:
                    con.execute("INSERT INTO prices VALUES (?,?,?,?,?)", (stock, day, value.raw, value.state, ordinal))
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Duplicate selected source stock/date key") from exc
                counts["cached_stock_rows"] += 1
        con.commit()
        counts["source_dates"] = len(dates)
        counts["source_calendar_dates_missing"] = len({d for d in sessions if min(dates) <= d <= max(dates)} - dates) if dates else len(sessions)
        if not counts["source_rows"]:
            raise ValueError("Empty corrected source")
    except Exception:
        con.close(); raise
    for day, m in market.items():
        m["date_consistent"] = m["value"] is not None and m["conflicting"] == 0 and m["invalid"] == 0
        value = m.pop("value")
        m["candidate_value"] = str(value) if value is not None else ""
    return con, market, dict(counts)


def price_batch(day, stocks, row_index, history, con, market, enable_market):
    """Only market sessions <= prediction date enter feature tensors."""
    n, t = len(stocks), len(history)
    a = {"seq": np.zeros((n, t, 1), "float32"), "seq_mask": np.zeros((n, t, 1), bool),
         "market_seq": np.zeros((1, t, 1), "float32"), "market_mask": np.zeros((1, t, 1), bool),
         "sector_seq": np.zeros((1, t, 1), "float32"), "sector_mask": np.zeros((1, t, 1), bool),
         "market_index": np.zeros(n, "int64"), "sector_index": np.zeros(n, "int64"),
         "sector_key": np.asarray(["UNKNOWN"], dtype="U7"), "stock_code": np.asarray(stocks, dtype="U6"),
         "prediction_member": np.ones(n, bool), "supervised": np.zeros(n, bool),
         "supervised_candidate": np.zeros(n, bool), "supervised_certified": np.zeros(n, bool),
         "label_bin": np.full(n, -1, "int64"), "outcome_status": np.empty(n, dtype="U40"),
         "unknown_reason": np.empty(n, dtype="U160"), "outcome_end_ordinal": np.zeros(n, "int64"),
         "observed_outcome_end_ordinal": np.zeros(n, "int64"),
         "events": np.empty((n, 0, 1), "float32"), "event_mask": np.empty((n, 0), bool),
         "body_available": np.empty((n, 0), bool), "event_json_valid": np.empty((n, 0), bool),
         "predicted_fields": np.empty((n, 0, 0), "float32"), "field_mask": np.empty((n, 0, 0), bool),
         "event_age": np.empty((n, 0), "float32"), "body_quality": np.empty((n, 0), "float32"),
         "evidence_quality": np.empty((n, 0), "float32"), "event_body_sha256": np.empty((n, 0), dtype="U64"),
         "event_cache_key": np.empty((n, 0), dtype="U64"), "edges": np.empty((2, 0), "int64"),
         "edge_share": np.empty(0, "float32"), "edge_age": np.empty(0, "float32"),
         "edge_accepted_complete": np.empty(0, bool), "graph_age": np.zeros(n, "float32"), "graph_quality": np.zeros(n, "float32")}
    for prefix, shape in (("seq", (n, t, 1)), ("market", (1, t, 1)), ("sector", (1, t, 1)), ("event", (n, 0)), ("edge", (0,))):
        for suffix in ("source", "available"):
            a[prefix + "_" + suffix + "_ordinal"] = np.zeros(shape, "int64")
    for suffix in ("source", "available", "effective"):
        a["sector_mapping_" + suffix + "_ordinal"] = np.zeros(n, "int64")
    provenance = []
    for i, stock in enumerate(stocks):
        row = row_index[(day, stock)]
        a["label_bin"][i] = int(row["loss_bin"] or -1)
        a["supervised_candidate"][i] = row["supervised_candidate"] == "1"
        for field in ("outcome_status", "unknown_reason"):
            a[field][i] = row[field]
        for field in ("outcome_end_ordinal", "observed_outcome_end_ordinal"):
            a[field][i] = int(row[field] or 0)
        cached = {r[0]: r[1:] for r in con.execute("SELECT day,raw,state,source_record FROM prices WHERE stock=?", (stock,))}
        for j, hday in enumerate(history):
            if hday is None:
                continue  # Left padding is masked, never backfilled from future observations.
            if hday > day:
                raise ValueError("Future observation in price history")
            raw, state, ordinal = cached.get(hday, ("", "absent_observation", 0))
            observed = parse_return(raw, ordinal)
            if state == "valid":
                value = float(observed.value)
                if not np.isfinite(np.float32(value)):
                    raise ValueError("Observed raw return cannot be represented by float32")
                a["seq"][i, j, 0] = value; a["seq_mask"][i, j, 0] = True
                a["seq_source_ordinal"][i, j, 0] = a["seq_available_ordinal"][i, j, 0] = date.fromisoformat(hday).toordinal()
            provenance.append({"prediction_date": day, "stock_code": stock, "history_date": hday,
                               "feature": "raw_Dret_candidate", "source_record_ordinal": ordinal, "raw": raw, "state": state,
                               "index_id": row["index_id"], "index_row_ordinal": row["row_ordinal"]})
    for j, hday in enumerate(history):
        if hday is None:
            continue
        m = market[hday]
        if enable_market and m["date_consistent"]:
            value = float(m["candidate_value"])
            if not np.isfinite(np.float32(value)):
                raise ValueError("Observed market candidate cannot be represented by float32")
            a["market_seq"][0, j, 0] = value; a["market_mask"][0, j, 0] = True
            a["market_source_ordinal"][0, j, 0] = a["market_available_ordinal"][0, j, 0] = date.fromisoformat(hday).toordinal()
    return {key: a[key] for key in sorted(ROLES)}, provenance


def build_candidate_batches(*, returns, calendar, panel_report, index_report, views_report, index_view,
                            fold, dates, stocks, output_dir, history_sessions=60, market_column="market_return_float",
                            market_candidate_context=False, forward_cpu=False):
    if history_sessions < 1 or history_sessions > 252:
        raise ValueError("Explicit limited history must be 1..252 market sessions")
    days, stocks = sorted(set(dates)), sorted(set(stocks))
    if not days or not stocks or len(days) > 20 or len(stocks) > 100:
        raise ValueError("Limited validation requires 1..20 explicit dates and 1..100 stocks")
    for day in days:
        canonical_day(day)
    for stock in stocks:
        source_key(stock, days[0])
    if market_column not in {"market_return_total", "market_return_float"}:
        raise ValueError("Use an explicit raw market column, never rmrf or a proxy")
    before, pr, fold_info, sessions, panel, parent_index = checked_sources(
        returns, calendar, panel_report, index_report, views_report, index_view, fold)
    positions = {d: i for i, d in enumerate(sessions)}
    if any(day not in positions for day in days):
        raise ValueError("Prediction date is not an observed market session")
    selected, boundaries = select_index(index_view, days, stocks, pr["target_version_id"])
    history = {}
    for day in days:
        pos = positions[day]
        available = sessions[max(0, pos - history_sessions + 1):pos + 1]
        history[day] = [None] * (history_sessions - len(available)) + available
    needed = {day for window in history.values() for day in window if day is not None}
    out = new_private_directory(output_dir); out.chmod(0o700)
    con, market, source_counts = stream_window_cache(returns, out / "price_window_cache.sqlite", sessions, needed, stocks, market_column)
    if source_counts["source_rows"] != pr["source_profile"]["rows"] or source_counts["source_calendar_dates_missing"]:
        con.close(); raise ValueError("Corrected source/calendar row/date profile differs from completed panel")
    items, provenance, sample, history_counts = [], [], None, {}
    try:
        for day in days:
            a, records = price_batch(day, stocks, selected, history[day], con, market, market_candidate_context)
            sample = a; provenance.extend(records)
            role = {selected[(day, stock)]["split"] for stock in stocks}
            if len(role) != 1:
                raise ValueError("One date cannot have different fold roles")
            path = out / (day + ".npz"); np.savez_compressed(path, **a)
            items.append({"path": path.name, "sha256": sha256(path), "date": day, "split": role.pop()})
            history_counts[day] = {stock: int(a["seq_mask"][i].sum()) for i, stock in enumerate(stocks)}
    finally:
        con.close()
    with (out / "record_provenance.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(provenance[0])); writer.writeheader(); writer.writerows(provenance)
    write_json(out / "market_context_audit.json", {"source_column": market_column, "source_sha256": before[str(Path(returns).resolve())],
               "policy": "date_consistent_raw_candidate" if market_candidate_context else "masked_identity_units_unverified",
               "market_identity_certified": False, "return_units_certified": False, "proxy_constructed": False,
               "history_date_records": market, "source_counts": source_counts})
    write_json(out / "unavailable_event_cache.json", {"status": "unavailable_no_frozen_model", "records": {},
               "model_sha256": None, "tokenizer_sha256": None, "template_sha256": None, "body_scope": None})
    artifacts = [{"path": str(path), "sha256": digest} for path, digest in before.items()]
    artifacts.extend({"path": name, "sha256": sha256(out / name)} for name in ("price_window_cache.sqlite", "record_provenance.csv", "market_context_audit.json"))
    manifest = {"schema": SCHEMA, "target_version": "actual-loss-four-bin-v1-2026-11-26",
                "source_target_version_id": pr["target_version_id"], "task": "actual_five_market_session_loss_four_bins",
                "scope": "candidate_panel_not_certified", "purpose": "candidate_input_validation_only",
                "universe_status": "OBSERVED_UNVERIFIED", "formal_training_allowed": False, "universe_certified": False,
                "explicit_selection": {"dates": days, "stock_codes": stocks}, "history_sessions": history_sessions,
                "validation_year": date.fromisoformat(fold_info["selection_start"]).year, "fold": str(fold), "split_boundaries": boundaries,
                "sector_mapping_status": "unavailable_no_certified_history", "source_artifacts": artifacts,
                "price_availability_policy": "source_daily_close_assumed_available_at_prediction_day_close_unverified",
                "market_context_policy": "date_consistent_raw_candidate" if market_candidate_context else "masked_identity_units_unverified",
                "quality_sources": {"body_quality": "source_body_traceability", "evidence_quality": "literal_evidence_traceability"},
                "fields": [{"name": key, "dtype": str(sample[key].dtype), "role": ROLES[key]} for key in sample],
                "feature_order": {"stock": [{"name": "raw_Dret_candidate", "depends_on": ["stock"]}],
                    "market": [{"name": "raw_" + market_column + "_candidate", "depends_on": ["market"]}],
                    "sector": [{"name": "unavailable_sector_return", "depends_on": ["sector"]}], "predicted": []},
                "event_dim": 1, "batches": items,
                "prediction_index": {"path": str(Path(index_view).resolve()), "sha256": before[str(Path(index_view).resolve())]},
                "frozen_event_cache": {"path": "unavailable_event_cache.json", "sha256": sha256(out / "unavailable_event_cache.json")}}
    write_json(out / "manifest.json", manifest)
    contract = RiskBatchContract(out / "manifest.json")
    forward = []
    torch.set_num_threads(2); torch.manual_seed(42)
    model = DisclosureModel(features=1, market_features=1, sector_features=1, event_dim=1, mode="price_only") if forward_cpu else None
    if model is not None:
        model.eval()
    for item in contract.items:
        b, _ = contract.load(item, "cpu", "price_only")
        if model is not None:
            with torch.inference_mode():
                result = model(b)
            if not torch.isfinite(result["probability"]).all() or not torch.allclose(result["probability"].sum(1), torch.ones(len(stocks))):
                raise ValueError("Untrained CPU forward failed probability shape/normalization check")
            forward.append({"date": item["date"], "output_shape": list(result["probability"].shape), "finite_normalized": True})
    contract.assert_unchanged(); unchanged(before)
    report = {"scope": "limited_REAL_observed_unverified_candidate_input_validation_ONLY", "validation_passed": True,
              "formal_training_allowed": False, "supervised_certified_rows": 0, "model_training_performed": False,
              "real_risk_scores_emitted": False, "forward_weights": "untrained_random_shape_check_only" if forward_cpu else "not_run",
              "explicit_selection": manifest["explicit_selection"], "rows": len(selected), "history_sessions": history_sessions,
              "history_valid_counts": history_counts, "market_context_policy": manifest["market_context_policy"],
              "market_date_conflicts": sum(m["conflicting"] > 0 for m in market.values()),
              "sector_status": "UNKNOWN_fully_masked_no_latest_category_backfill", "event_graph_status": "unavailable_empty_not_fabricated",
              "source_counts": source_counts, "forward": forward, "identity": contract.identity,
              "code_sha256": fingerprints([Path(__file__), Path(__file__).with_name("risk_batch_contract.py"), Path(__file__).with_name("research_model.py")])}
    write_json(out / "candidate_batch_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("returns", "calendar", "panel-report", "index-report", "views-report", "index-view", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--fold", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--stocks", nargs="+", required=True)
    parser.add_argument("--history-sessions", type=int, default=60)
    parser.add_argument("--market-column", choices=["market_return_total", "market_return_float"], default="market_return_float")
    parser.add_argument("--market-candidate-context", action="store_true")
    parser.add_argument("--forward-cpu", action="store_true")
    args = parser.parse_args()
    report = build_candidate_batches(**vars(args))
    print(json.dumps({key: value for key, value in report.items() if key not in {"identity", "code_sha256"}}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
