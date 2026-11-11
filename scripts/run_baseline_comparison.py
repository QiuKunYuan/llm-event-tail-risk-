#!/usr/bin/env python3
"""Run same-sample, walk-forward baselines for the RESSET tail-risk labels.

All models use the same company-day observations, p10 target, temporal folds,
and as-of-t feature set. Features added from announcements or ownership can be
plugged into this comparison later without changing the evaluation protocol.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


DEFAULT_DATA = Path("/home/admin/workspace/RESSET论文数据/processed/tail_risk_labels.csv.gz")
DEFAULT_OUTPUT = Path("/home/admin/workspace/RESSET论文数据/processed/baseline_comparison")
FEATURES = [
    "return_1d",
    "cumulative_return_5d",
    "cumulative_return_20d",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_60d",
    "valid_returns_20d",
    "valid_returns_60d",
    "rmrf_float",
    "smb_float",
    "hml_float",
]
TARGET = "label_p10"
TEST_YEARS = (2021, 2022, 2023, 2024)
PURGE_TRADING_DAYS = 5


def metrics(y_true: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    """Return ranking, alert-capacity, and probability-calibration metrics."""
    top_n = max(1, int(np.ceil(len(scores) * 0.10)))
    positives = int(y_true.sum())
    if np.all(scores == scores[0]):
        # A constant score has no ranking. Report the expected result under a
        # random 10% alert budget instead of letting row order break the tie.
        precision_top10 = float(y_true.mean())
        recall_top10 = 0.10
    else:
        top_idx = np.argpartition(scores, -top_n)[-top_n:]
        precision_top10 = float(y_true[top_idx].mean())
        recall_top10 = float(y_true[top_idx].sum() / positives) if positives else 0.0
    return {
        "average_precision": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "brier": float(brier_score_loss(y_true, scores)),
        "precision_at_top10pct": precision_top10,
        "recall_at_top10pct": recall_top10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(TEST_YEARS),
        help="out-of-time test years (training expands through the prior years)",
    )
    args = parser.parse_args()
    if not args.data.exists():
        raise FileNotFoundError(args.data)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Limit BLAS/OpenMP thread pools before fitting CPU models.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[variable] = str(args.threads)

    columns = ["stock_code", "date", TARGET, *FEATURES]
    dtypes = {name: "float32" for name in FEATURES}
    dtypes[TARGET] = "uint8"
    dtypes["stock_code"] = "string"
    print(f"Reading {args.data}", flush=True)
    frame = pd.read_csv(
        args.data,
        usecols=columns,
        dtype=dtypes,
        parse_dates=["date"],
        compression="infer",
    )
    frame = frame.dropna(subset=[TARGET])
    frame[TARGET] = frame[TARGET].astype("uint8")
    dates = pd.DatetimeIndex(frame["date"].drop_duplicates().sort_values())
    date_positions = {date: pos for pos, date in enumerate(dates)}
    print(
        f"Loaded {len(frame):,} rows, {len(dates):,} market dates; "
        f"overall p10 prevalence={frame[TARGET].mean():.4%}",
        flush=True,
    )

    feature_matrix = frame[FEATURES].to_numpy(dtype=np.float32, copy=True)
    target = frame[TARGET].to_numpy(dtype=np.uint8, copy=True)
    results: list[dict[str, object]] = []
    prediction_frames: list[pd.DataFrame] = []

    for year in sorted(set(args.years)):
        test_mask = frame["date"].dt.year.to_numpy() == year
        if not test_mask.any():
            print(f"Skipping {year}: no test rows", flush=True)
            continue
        first_test_date = pd.Timestamp(frame.loc[test_mask, "date"].min())
        first_test_pos = date_positions[first_test_date]
        cutoff_pos = max(0, first_test_pos - PURGE_TRADING_DAYS)
        train_cutoff = dates[cutoff_pos]
        train_mask = frame["date"].to_numpy() < np.datetime64(train_cutoff)
        if not train_mask.any():
            raise ValueError(f"No training rows before {year} after purging")
        if np.unique(target[train_mask]).size != 2 or np.unique(target[test_mask]).size != 2:
            raise ValueError(f"Both classes must exist in train and test for {year}")

        x_train = feature_matrix[train_mask]
        y_train = target[train_mask]
        x_test = feature_matrix[test_mask]
        y_test = target[test_mask]
        train_rate = float(y_train.mean())
        print(
            f"Fold {year}: train={int(train_mask.sum()):,} rows before "
            f"{pd.Timestamp(train_cutoff).date()} (exclusive; purge={PURGE_TRADING_DAYS} sessions); "
            f"test={int(test_mask.sum()):,} rows from {first_test_date.date()}",
            flush=True,
        )

        # Constant prevalence is the no-skill reference. Downside volatility
        # alone is a transparent persistence baseline. Logistic and LightGBM
        # use exactly the same rows and full feature matrix.
        scores_by_model: dict[str, np.ndarray] = {
            "prevalence": np.full(len(y_test), train_rate, dtype=np.float32)
        }

        one_feature_idx = [FEATURES.index("downside_volatility_60d")]
        persistence = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(C=1.0, max_iter=100, solver="lbfgs"),
        )
        with threadpool_limits(limits=args.threads):
            persistence.fit(x_train[:, one_feature_idx], y_train)
            scores_by_model["logit_downside_volatility"] = persistence.predict_proba(
                x_test[:, one_feature_idx]
            )[:, 1].astype(np.float32)

        logistic = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(C=1.0, max_iter=100, solver="lbfgs"),
        )
        with threadpool_limits(limits=args.threads):
            logistic.fit(x_train, y_train)
            scores_by_model["logistic_all_features"] = logistic.predict_proba(x_test)[:, 1].astype(
                np.float32
            )

        tree = LGBMClassifier(
            objective="binary",
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            max_bin=63,
            min_child_samples=100,
            reg_lambda=1.0,
            colsample_bytree=0.9,
            subsample=0.8,
            subsample_freq=1,
            n_jobs=args.threads,
            random_state=20261106,
            verbosity=-1,
            force_col_wise=True,
        )
        with threadpool_limits(limits=args.threads):
            tree.fit(x_train, y_train)
            scores_by_model["lightgbm_all_features"] = tree.predict_proba(x_test)[:, 1].astype(
                np.float32
            )

        fold_metadata = {
            "test_year": year,
            "train_rows": int(train_mask.sum()),
            "train_first_date": str(pd.Timestamp(frame.loc[train_mask, "date"].min()).date()),
            "train_last_date": str(pd.Timestamp(frame.loc[train_mask, "date"].max()).date()),
            "train_cutoff_exclusive": str(pd.Timestamp(train_cutoff).date()),
            "train_positive_rate": train_rate,
            "test_rows": int(test_mask.sum()),
            "test_first_date": str(first_test_date.date()),
            "test_last_date": str(pd.Timestamp(frame.loc[test_mask, "date"].max()).date()),
            "test_positive_rate": float(y_test.mean()),
            "purge_trading_days": PURGE_TRADING_DAYS,
        }
        for model_name, scores in scores_by_model.items():
            row: dict[str, object] = {**fold_metadata, "model": model_name, **metrics(y_test, scores)}
            results.append(row)
            print(
                f"  {model_name}: AP={row['average_precision']:.5f}, "
                f"ROC-AUC={row['roc_auc']:.5f}, Brier={row['brier']:.5f}, "
                f"Recall@Top10%={row['recall_at_top10pct']:.4f}",
                flush=True,
            )

        prediction = frame.loc[test_mask, ["stock_code", "date", TARGET]].copy()
        for model_name, scores in scores_by_model.items():
            prediction[model_name] = scores
        prediction["test_year"] = year
        prediction_frames.append(prediction)

    if not results:
        raise ValueError("No test folds were evaluated")
    metrics_frame = pd.DataFrame(results)
    metrics_path = args.output_dir / "metrics_by_fold.csv"
    metrics_frame.to_csv(metrics_path, index=False)
    prediction_path = args.output_dir / "oos_predictions.csv.gz"
    all_predictions = pd.concat(prediction_frames, ignore_index=True)
    all_predictions.to_csv(prediction_path, index=False, compression="gzip")
    model_names = ["prevalence", "logit_downside_volatility", "logistic_all_features", "lightgbm_all_features"]
    metric_names = [
        "average_precision",
        "roc_auc",
        "brier",
        "precision_at_top10pct",
        "recall_at_top10pct",
    ]
    annual_mean_metrics = {
        name: {
            metric_name: float(metrics_frame.loc[metrics_frame["model"] == name, metric_name].mean())
            for metric_name in metric_names
        }
        for name in model_names
    }
    annual_mean_path = args.output_dir / "metrics_mean_by_year.json"
    annual_mean_path.write_text(
        json.dumps(annual_mean_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = {
        "data": str(args.data),
        "target": TARGET,
        "features": FEATURES,
        "test_years_requested": sorted(set(args.years)),
        "purge_trading_days": PURGE_TRADING_DAYS,
        "models": [
            "prevalence",
            "logit_downside_volatility",
            "logistic_all_features",
            "lightgbm_all_features",
        ],
        "metrics": [
            "average_precision",
            "roc_auc",
            "brier",
            "precision_at_top10pct",
            "recall_at_top10pct",
        ],
        "hyperparameters_fixed_without_test_tuning": True,
        "metrics_csv": str(metrics_path),
        "predictions_csv_gzip": str(prediction_path),
        "mean_by_year_metrics": str(annual_mean_path),
        "mean_test_year_metrics": annual_mean_metrics,
        "folds": results,
    }
    report_path = args.output_dir / "run_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved metrics: {metrics_path}", flush=True)
    print(f"Saved OOS predictions: {prediction_path}", flush=True)
    print(f"Saved run report: {report_path}", flush=True)


if __name__ == "__main__":
    main()
