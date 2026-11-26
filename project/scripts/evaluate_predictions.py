"""Actual-loss evaluation, unknown-outcome coverage and paired moving date blocks.

Primary: AP/NLL/Brier and Vcal-frozen absolute alert recall/FNR for loss, 5%, 10%.
Daily Top10% is secondary and selected from the prediction universe *before*
filtering unknown outcomes. Market/industry mean probabilities are expected loss
member fractions, not index-loss probabilities. Seed metrics and folds are averaged
equally, never probabilities. Unknown alerts are neither false positives nor safe.
"""
from __future__ import annotations

import argparse
import json
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from probability_calibration import BIN_COLUMNS, PROBABILITY_COLUMNS, cumulative_probabilities, normalized_probabilities


def _average(values):
    return float(np.mean(values)) if values and all(x is not None for x in values) else None


def _known_binary(frame, label="label"):
    values = pd.to_numeric(frame[label], errors="raise")
    known = values.notna() & (values != -1)
    if not values[known].isin([0, 1]).all():
        raise ValueError("Known binary labels must be 0/1; unknown is empty or -1")
    return known, values


def metrics(frame, column, threshold=None, label="label"):
    """Binary target metrics on observable outcomes, capacity selection on all rows."""
    p = frame[column].to_numpy(dtype=float)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Every universe member needs a finite actual probability")
    known, values = _known_binary(frame, label)
    y, q = values[known].to_numpy(dtype=int), p[known.to_numpy()]
    if threshold is not None and (not np.isfinite(threshold) or not 0 <= threshold <= 1):
        raise ValueError("Invalid frozen alert threshold")
    n, o, b = len(frame), int(known.sum()), int(y.sum())
    daily_precision, daily_recall, top_details = [], [], []
    for day, g in frame.groupby("date", sort=True):
        selected = g.sort_values([column, "stock_code"], ascending=[False, True]).iloc[:max(1, int(np.ceil(len(g) * .1)))]
        sk, sy = _known_binary(selected, label)
        gk, gy = _known_binary(g, label)
        positives, observable, count = int(sy[sk].sum()), int(sk.sum()), len(selected)
        all_pos = int(gy[gk].sum())
        daily_precision.append(positives / observable if observable else None)
        daily_recall.append(positives / all_pos if all_pos else None)
        top_details.append({"date": str(day), "selected_from_universe": count, "selected_observable": observable,
                            "selected_unknown": count - observable, "observed_positive": positives,
                            "precision_bounds": [positives / count, (positives + count - observable) / count]})
    alert = p >= threshold if threshold is not None else np.zeros(n, bool)
    tp = int((alert[known] & (y == 1)).sum())
    fn = int((~alert[known] & (y == 1)).sum())
    fp = int((alert[known] & (y == 0)).sum())
    negatives = int((y == 0).sum())
    tiny = np.finfo(float).eps
    return {"AP": float(average_precision_score(y, q)) if b else None,
            "ROC_AUC": float(roc_auc_score(y, q)) if len(np.unique(y)) == 2 else None,
            "NLL": float(-(y * np.log(np.clip(q, tiny, 1)) + (1 - y) * np.log(np.clip(1 - q, tiny, 1))).mean()) if o else None,
            "Brier": float(((q - y) ** 2).mean()) if o else None,
            "threshold": threshold, "threshold_recall": tp / b if threshold is not None and b else None,
            "FNR": fn / b if threshold is not None and b else None,
            "FPR": fp / negatives if threshold is not None and negatives else None,
            "known_false_alerts": fp if threshold is not None else None,
            "unknown_alerts": int((alert & ~known.to_numpy()).sum()) if threshold is not None else None,
            "alert_universe_count": int(alert.sum()) if threshold is not None else None,
            "universe_N": n, "observable_O": o, "known_positives_B": b, "unknown_M": n - o,
            "observable_fraction": o / n if n else None,
            "actual_positive_fraction_bounds": [b / n, (b + n - o) / n] if n else None,
            "observable_positive_fraction": b / o if o else None,
            "mean_probability_observable_subset": float(q.mean()) if o else None,
            "mean_probability_universe": float(p.mean()) if n else None,
            "daily_top10_precision": _average([x for x in daily_precision if x is not None]),
            "daily_top10_recall": _average([x for x in daily_recall if x is not None]),
            "daily_top10_selection": top_details}


def fold_groups(frame):
    if "fold" not in frame:
        if frame.date.astype(str).str[:4].nunique() > 1:
            raise ValueError("Multi-year predictions require explicit fold ids")
        return [("single", frame)]
    if frame.fold.isna().any() or (frame.groupby("date").fold.nunique() > 1).any():
        raise ValueError("Each market date must belong to exactly one fold")
    return list(frame.groupby("fold", sort=True))


def moving_block_dates(days, block, rng):
    """Ordinary overlapping MOVING blocks (not fixed nonoverlapping bins).

    All possible consecutive starts are sampled; concatenate then truncate to the
    original date count. A block longer than a fold is that fold's whole date set.
    """
    days = np.asarray(sorted(days))
    if not len(days) or block < 1:
        raise ValueError("Need dates and a positive moving block length")
    length = min(block, len(days))
    starts = rng.integers(0, len(days) - length + 1, size=int(np.ceil(len(days) / length)))
    sampled = np.concatenate([days[start:start + length] for start in starts])[:len(days)]
    return sampled, starts


def paired_ci(frame, model, baseline, block=20, repeats=1000, seed=42, label="label"):
    if block < 1 or repeats < 1:
        raise ValueError("Positive block size and repeat count required")
    models = [model] if isinstance(model, str) else list(model)
    baselines = [baseline] if isinstance(baseline, str) else list(baseline)
    rng = np.random.default_rng(seed)
    groups = []
    for _, g in fold_groups(frame):
        known, values = _known_binary(g, label)
        if not values[known].sum():
            raise ValueError("Bootstrap AP requires observable positives in each fold")
        groups.append((g, sorted(g.date.unique()), known, values))
    delta = []
    for _ in range(repeats):
        differences = []
        for g, days, known, values in groups:
            sampled, _ = moving_block_dates(days, block, rng)
            unique, multiplicity = np.unique(sampled, return_counts=True)
            counts = dict(zip(unique, multiplicity))
            # One weight per entire market date, shared by model/baseline/seeds.
            weights = g.date.map(counts).fillna(0).to_numpy(dtype=int)[known]
            y = values[known].to_numpy(dtype=int)
            if not (y * weights).sum():
                break
            model_ap = np.mean([average_precision_score(y, g.loc[known, c], sample_weight=weights) for c in models])
            base_ap = np.mean([average_precision_score(y, g.loc[known, c], sample_weight=weights) for c in baselines])
            differences.append(model_ap - base_ap)
        if len(differences) == len(groups):
            delta.append(float(np.mean(differences)))
    if not delta:
        raise ValueError("No valid moving-block replicates")
    return {"method": "paired_whole_market_date_overlapping_moving_block_bootstrap",
            "block_sessions": block, "requested_repeats": repeats, "valid_repeats": len(delta), "seed": seed,
            "statistic": "equal_fold_mean_seed_mean_delta_AP", "delta_AP_CI95": np.quantile(delta, [.025, .975]).tolist()}


def _validate_frame(frame, columns):
    if frame.empty or frame[["stock_code", "date"]].isna().any().any():
        raise ValueError("Empty input or missing prediction keys")
    if frame.duplicated(["stock_code", "date"]).any():
        raise ValueError("Duplicate company-day predictions")
    if not np.isfinite(frame[columns].to_numpy(dtype=float)).all() or not frame[columns].apply(lambda c: c.between(0, 1).all()).all():
        raise ValueError("Predictions must be finite probabilities for all universe rows")


def evaluate(frame, models, baseline, repeats=1000, blocks=(20, 5, 60), threshold=None, label="label"):
    """Paired binary comparison API (also retained for external baseline callers)."""
    models = [models] if isinstance(models, str) else list(models)
    baselines = [baseline] if isinstance(baseline, str) else list(baseline)
    _validate_frame(frame, models + baselines)
    _known_binary(frame, label)
    folds = []
    for name, g in fold_groups(frame):
        base_runs = {c: metrics(g, c, threshold, label) for c in baselines}
        runs = {c: metrics(g, c, threshold, label) for c in models}
        base_ap = _average([r["AP"] for r in base_runs.values()])
        mean = _average([v["AP"] for v in runs.values()])
        folds.append({"fold": str(name), "rows": len(g), "models": runs,
                      "baseline": {"AP": base_ap}, "baseline_by_seed": base_runs, "seed_mean_AP": mean,
                      "seed_AP_std": float(np.std([v["AP"] for v in runs.values()], ddof=1)) if len(runs) > 1 and mean is not None else None,
                      "delta_AP": mean - base_ap if mean is not None and base_ap is not None else None})
    by_seed = {c: _average([f["models"][c]["AP"] for f in folds]) for c in models}
    comparable = all(f["delta_AP"] is not None for f in folds)
    return {"folds": folds, "equal_fold_model_AP": _average(list(by_seed.values())),
            "equal_fold_baseline_AP": _average([f["baseline"]["AP"] for f in folds]),
            "equal_fold_delta_AP": _average([f["delta_AP"] for f in folds]), "equal_fold_AP_by_seed": by_seed,
            "pooled_AP_by_seed": {c: metrics(frame, c, threshold, label)["AP"] for c in models},
            "pooled_baseline_AP": _average([metrics(frame, c, threshold, label)["AP"] for c in baselines]),
            "equal_fold_baseline_AP_by_seed": {c: _average([f["baseline_by_seed"][c]["AP"] for f in folds]) for c in baselines},
            "CI": [paired_ci(frame, models, baselines, b, repeats, label=label) for b in blocks] if comparable else [],
            "CI_status": "available" if comparable else "undefined_observable_positives_missing"}


def risk_aggregates(frame):
    """Date and historical industry expected member fractions, with coverage bounds."""
    result = {"interpretation": "expected_loss_member_fraction_NOT_index_loss_probability", "market": [], "industry": []}
    groupings = [("market", ["date"])]
    if "industry" in frame:
        if frame.industry.isna().any():
            raise ValueError("Historical industry key must be explicit (including unknown category)")
        groupings.append(("industry", ["date", "industry"]))
    for kind, keys in groupings:
        for key, g in frame.groupby(keys, sort=True, dropna=False):
            record = dict(zip(keys, map(str, key if isinstance(key, tuple) else (key,))))
            for probability in PROBABILITY_COLUMNS:
                m = metrics(g, probability, label=probability + "_label")
                record[probability] = {k: m[k] for k in ("universe_N", "observable_O", "known_positives_B", "unknown_M", "observable_fraction",
                    "actual_positive_fraction_bounds", "observable_positive_fraction", "mean_probability_observable_subset", "mean_probability_universe")}
            if "outcome_status" in g:
                record["outcome_status_counts"] = {str(k): int(v) for k, v in g.outcome_status.value_counts(dropna=False).items()}
            result[kind].append(record)
    return result


def evaluate_risk(frame, thresholds=None, model_prefixes=("",), baseline_prefixes=None, repeats=1000, blocks=(20, 5, 60)):
    """Four-bin primary API; prefixes identify separate predeclared seed columns."""
    frame = frame.copy()
    labels = pd.to_numeric(frame.label_bin, errors="raise")
    known = labels.notna() & (labels != -1)
    frame["label_bin"] = labels
    if "prediction_member" in frame and not frame.prediction_member.isin([1, True]).all():
        raise ValueError("Evaluation must contain prediction-universe members, not sampled neighbors")
    if not labels[known].isin([0, 1, 2, 3]).all():
        raise ValueError("Four-bin outcome labels must be 0..3 or unknown")
    if "outcome_status" in frame:
        status_known = frame.outcome_status.isin(["known", "complete_raw_return_candidate"])
        if not np.array_equal(known, status_known):
            raise ValueError("Outcome status and labels disagree")
    thresholds = thresholds or {}
    for meta in thresholds.values():
        if meta.get("split_role") != "calibration" or meta.get("criterion") not in {"F2", "validation_tradeoff"}:
            raise ValueError("Absolute alert thresholds must be frozen from Vcal, never test")
    for name, target in zip(PROBABILITY_COLUMNS, ((labels > 0), (labels >= 2), (labels == 3))):
        frame[name + "_label"] = target.astype(float).where(known, np.nan)
    prefixes = list(model_prefixes) + list(baseline_prefixes or [])
    for prefix in prefixes:
        columns = [prefix + c for c in BIN_COLUMNS]
        _validate_frame(frame, columns)
        p = normalized_probabilities(frame[columns].to_numpy())
        for name, probability in cumulative_probabilities(p).items():
            column = prefix + name
            if column in frame and not np.allclose(frame[column], probability, rtol=0, atol=1e-6):
                raise ValueError("Cumulative risk output differs from actual normalized bins")
            frame[column] = probability
    fold_reports = []
    for fold, group in fold_groups(frame):
        kg = group.label_bin.notna() & (group.label_bin != -1)
        models = {}
        for prefix in model_prefixes:
            p = group.loc[kg, [prefix + c for c in BIN_COLUMNS]].to_numpy(dtype=float)
            y = group.loc[kg, "label_bin"].to_numpy(dtype=int)
            models[prefix or "model"] = {
                "four_bin_NLL": float(-np.log(np.maximum(p[np.arange(len(y)), y], np.finfo(float).tiny)).mean()) if len(y) else None,
                "four_bin_Brier": float(((p - np.eye(4)[y]) ** 2).sum(1).mean()) if len(y) else None,
                "targets": {name: metrics(group, prefix + name, thresholds.get(name, {}).get("threshold"), name + "_label") for name in PROBABILITY_COLUMNS}}
        fold_reports.append({"fold": str(fold), "models": models})
    summary = {}
    for name in PROBABILITY_COLUMNS:
        seed_summary = {prefix or "model": {metric: _average([f["models"][prefix or "model"]["targets"][name][metric] for f in fold_reports])
            for metric in ("AP", "NLL", "Brier", "threshold_recall", "FNR", "FPR")} for prefix in model_prefixes}
        summary[name] = {"equal_fold_by_seed": seed_summary,
                         "equal_fold_seed_mean": {metric: _average([value[metric] for value in seed_summary.values()]) for metric in next(iter(seed_summary.values()))}}
    four_bin_by_seed = {prefix or "model": {metric: _average([f["models"][prefix or "model"][metric] for f in fold_reports])
        for metric in ("four_bin_NLL", "four_bin_Brier")} for prefix in model_prefixes}
    four_bin_summary = {"equal_fold_by_seed": four_bin_by_seed,
                        "equal_fold_seed_mean": {metric: _average([value[metric] for value in four_bin_by_seed.values()])
                                                 for metric in ("four_bin_NLL", "four_bin_Brier")}}
    comparisons = {name: evaluate(frame, [p + name for p in model_prefixes], [p + name for p in baseline_prefixes], repeats, blocks,
                                  thresholds.get(name, {}).get("threshold"), name + "_label") for name in PROBABILITY_COLUMNS} if baseline_prefixes else {}
    # Separate aggregates for each seed, without interpreting a probability ensemble as seed metrics.
    aggregates = {}
    for prefix in model_prefixes:
        view = frame.copy()
        for name in PROBABILITY_COLUMNS:
            view[name] = frame[prefix + name]
        aggregates[prefix or "model"] = risk_aggregates(view)
    return {"task": "actual_five_market_session_loss_four_bins", "folds": fold_reports, "summary": summary,
            "four_bin_summary": four_bin_summary, "comparisons": comparisons, "aggregates_by_seed": aggregates, "threshold_metadata": thresholds,
            "unknown_outcomes_not_safe_or_false_alerts": True, "top10_role": "secondary_capacity_not_absolute_risk"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--thresholds", help="Frozen Vcal JSON; never fitted here")
    parser.add_argument("--model-prefixes", nargs="+", default=[""])
    parser.add_argument("--baseline-prefixes", nargs="+")
    parser.add_argument("--repeats", type=int, default=1000)
    args = parser.parse_args()
    frame = pd.read_csv(args.input, dtype={"stock_code": str, "date": str, "fold": str, "industry": str})
    thresholds = json.loads(open(args.thresholds, encoding="utf-8").read()) if args.thresholds else None
    print(json.dumps(evaluate_risk(frame, thresholds, args.model_prefixes, args.baseline_prefixes, args.repeats), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
