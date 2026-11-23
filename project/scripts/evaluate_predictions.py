"""Daily Top10% and paired market-day block bootstrap for OOS CSV."""
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
        daily.append((selected.label.mean(), selected.label.sum() / g.label.sum() if g.label.sum() else np.nan))
    return {"AP": float(average_precision_score(y, p)), "ROC_AUC": float(roc_auc_score(y, p)),
            "Brier": float(brier_score_loss(y, p)), "daily_top10_precision": float(np.mean([x[0] for x in daily])),
            "daily_top10_recall": float(np.nanmean([x[1] for x in daily]))}


def paired_ci(frame, model, baseline, block=20, repeats=1000, seed=42):
    days = sorted(frame.date.unique())
    # Non-overlapping blocks preserve within-day and local cross-day dependence.
    chunks = [frame[frame.date.isin(days[i:i+block])] for i in range(0, len(days), block)]
    rng = np.random.default_rng(seed)
    delta = []
    for _ in range(repeats):
        sample = pd.concat([chunks[i] for i in rng.integers(0, len(chunks), len(chunks))], ignore_index=True)
        if sample.label.nunique() != 2:
            continue
        delta.append(average_precision_score(sample.label, sample[model]) - average_precision_score(sample.label, sample[baseline]))
    if not delta:
        raise ValueError("No valid bootstrap replicates")
    return {"block_sessions": block, "valid_repeats": len(delta), "delta_AP_CI95": np.quantile(delta, [.025, .975]).tolist()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--model", default="prediction")
    p.add_argument("--baseline", default="baseline")
    p.add_argument("--repeats", type=int, default=1000)
    a = p.parse_args()
    f = pd.read_csv(a.input, dtype={"stock_code": str, "date": str})
    if f.duplicated(["stock_code", "date"]).any():
        raise ValueError("Duplicate company-day predictions")
    if not f.label.isin([0, 1]).all() or not np.isfinite(f[[a.model, a.baseline]]).all().all():
        raise ValueError("Invalid labels or predictions")
    if not f[[a.model, a.baseline]].apply(lambda c: c.between(0, 1).all()).all():
        raise ValueError("Predictions must be in [0,1]")
    print(json.dumps({"model": metrics(f, a.model), "baseline": metrics(f, a.baseline),
          "CI": [paired_ci(f, a.model, a.baseline, b, a.repeats) for b in [5, 20, 60]]}, indent=2))

if __name__ == "__main__":
    main()
