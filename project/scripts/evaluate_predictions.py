"""Daily Top10% and fold-stratified paired market-day block bootstrap.

A fold column is required for multi-year formal evaluation. Multiple seed columns
are averaged as metrics, not as probabilities. No checkpoint selection occurs here.
"""
import argparse
import json
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss


def metrics(frame, column):
    p, y = frame[column], frame.label
    daily = []
    for _, g in frame.groupby("date", sort=True):
        selected = g.sort_values([column, "stock_code"], ascending=[False, True]).head(max(1, int(np.ceil(len(g) * .1))))
        daily.append((selected.label.mean(), selected.label.sum() / g.label.sum() if g.label.sum() else None))
    recall = [x[1] for x in daily if x[1] is not None]
    return {"AP": float(average_precision_score(y, p)) if y.sum() else None,
            "ROC_AUC": float(roc_auc_score(y, p)) if y.nunique() == 2 else None,
            "Brier": float(brier_score_loss(y, p)),
            "daily_top10_precision": float(np.mean([x[0] for x in daily])),
            "daily_top10_recall": float(np.mean(recall)) if recall else None}


def fold_groups(frame):
    if "fold" not in frame:
        if frame.date.str[:4].nunique() > 1:
            raise ValueError("Multi-year predictions require explicit fold ids")
        return [("single", frame)]
    if frame.fold.isna().any() or (frame.groupby("date").fold.nunique() > 1).any():
        raise ValueError("Each market date must belong to exactly one fold")
    return list(frame.groupby("fold", sort=True))


def paired_ci(frame, model, baseline, block=20, repeats=1000, seed=42):
    if block < 1 or repeats < 1:
        raise ValueError("Positive block size and repeat count required")
    models = [model] if isinstance(model, str) else list(model)
    baselines = [baseline] if isinstance(baseline, str) else list(baseline)
    rng = np.random.default_rng(seed)
    groups = []
    for _, g in fold_groups(frame):
        if g.label.nunique() != 2:
            raise ValueError("Bootstrap needs both classes in each fold")
        days = sorted(g.date.unique())
        day_blocks = {d: i // block for i, d in enumerate(days)}
        index = g.date.map(day_blocks).to_numpy()
        groups.append((g, index, int(index.max()) + 1))
    delta = []
    for _ in range(repeats):
        differences = []
        for g, index, count in groups:
            # Multiplicities are equivalent to duplicating all rows of a day block.
            multiplicity = np.bincount(rng.integers(0, count, count), minlength=count)
            weights = multiplicity[index]
            if g.loc[weights > 0, "label"].nunique() != 2:
                break
            model_ap = np.mean([average_precision_score(g.label, g[c], sample_weight=weights) for c in models])
            base_ap = np.mean([average_precision_score(g.label, g[c], sample_weight=weights) for c in baselines])
            differences.append(model_ap - base_ap)
        if len(differences) == len(groups):
            delta.append(float(np.mean(differences)))
    if not delta:
        raise ValueError("No valid bootstrap replicates")
    return {"block_sessions": block, "valid_repeats": len(delta),
            "statistic": "equal_fold_mean_seed_mean_delta_AP",
            "delta_AP_CI95": np.quantile(delta, [.025, .975]).tolist()}


def evaluate(frame, models, baseline, repeats=1000, blocks=(5, 20, 60)):
    if frame.empty or frame[["stock_code", "date", "label"]].isna().any().any():
        raise ValueError("Empty input or missing prediction keys")
    if frame.duplicated(["stock_code", "date"]).any():
        raise ValueError("Duplicate company-day predictions")
    baselines = [baseline] if isinstance(baseline, str) else list(baseline)
    columns = models + baselines
    if not frame.label.isin([0, 1]).all() or not np.isfinite(frame[columns]).all().all():
        raise ValueError("Invalid labels or predictions")
    if not frame[columns].apply(lambda c: c.between(0, 1).all()).all():
        raise ValueError("Predictions must be in [0,1]")
    folds = []
    for name, g in fold_groups(frame):
        base_runs = {c: metrics(g, c) for c in baselines}
        base = {k: float(np.mean([r[k] for r in base_runs.values()]))
                if all(r[k] is not None for r in base_runs.values()) else None
                for k in next(iter(base_runs.values()))}
        runs = {c: metrics(g, c) for c in models}
        aps = [v["AP"] for v in runs.values()]
        if base["AP"] is None or any(a is None for a in aps):
            raise ValueError("Formal AP comparison needs positive labels in every fold")
        mean = float(np.mean(aps))
        folds.append({"fold": str(name), "rows": len(g), "positive_rate": float(g.label.mean()),
                      "models": runs, "baseline": base, "baseline_by_seed": base_runs, "seed_mean_AP": mean,
                      "seed_AP_std": float(np.std(aps, ddof=1)) if len(aps) > 1 else None,
                      "delta_AP": mean - base["AP"]})
    by_seed = {c: float(np.mean([f["models"][c]["AP"] for f in folds])) for c in models}
    return {"folds": folds, "equal_fold_model_AP": float(np.mean(list(by_seed.values()))),
            "equal_fold_baseline_AP": float(np.mean([f["baseline"]["AP"] for f in folds])),
            "equal_fold_delta_AP": float(np.mean([f["delta_AP"] for f in folds])),
            "equal_fold_AP_by_seed": by_seed,
            "pooled_AP_by_seed": {c: metrics(frame, c)["AP"] for c in models},
            "pooled_baseline_AP": float(np.mean([metrics(frame, c)["AP"] for c in baselines])),
            "equal_fold_baseline_AP_by_seed": {c: float(np.mean([f["baseline_by_seed"][c]["AP"] for f in folds])) for c in baselines},
            "CI": [paired_ci(frame, models, baseline, b, repeats) for b in blocks]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--model", default="prediction")
    p.add_argument("--seed-models", nargs="+", help="One prediction column per predeclared seed")
    p.add_argument("--baseline", default="baseline")
    p.add_argument("--seed-baselines", nargs="+", help="One baseline prediction column per predeclared seed")
    p.add_argument("--repeats", type=int, default=1000)
    a = p.parse_args()
    f = pd.read_csv(a.input, dtype={"stock_code": str, "date": str, "fold": str})
    print(json.dumps(evaluate(f, a.seed_models or [a.model], a.seed_baselines or a.baseline, a.repeats), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
