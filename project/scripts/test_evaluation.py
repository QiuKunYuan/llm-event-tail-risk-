"""Regression tests for equal-fold, paired and daily operational evaluation."""
import unittest
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from evaluate_predictions import evaluate, metrics, paired_ci


def frame():
    return pd.DataFrame({"stock_code": ["000001", "000002", "000003", "000004"] * 2,
                         "date": ["2021-01-04"] * 4 + ["2022-01-04"] * 4,
                         "fold": ["2021"] * 4 + ["2022"] * 4,
                         "label": [1, 0, 0, 0, 0, 1, 1, 1],
                         "prediction": [.9, .8, .7, .6, .9, .8, .7, .6],
                         "baseline": [.6, .7, .8, .9, .6, .7, .8, .9]})


class EvaluationTests(unittest.TestCase):
    def test_equal_fold_is_not_pooled_ap(self):
        f = frame(); r = evaluate(f, ["prediction"], "baseline", repeats=4, blocks=(1,))
        expected = np.mean([average_precision_score(g.label, g.prediction) for _, g in f.groupby("fold")])
        self.assertAlmostEqual(r["equal_fold_model_AP"], expected)
        self.assertNotAlmostEqual(r["pooled_AP_by_seed"]["prediction"], expected)
        self.assertEqual(r["CI"][0]["valid_repeats"], 4)
        lo, hi = r["CI"][0]["delta_AP_CI95"]
        self.assertAlmostEqual(lo, r["equal_fold_delta_AP"])
        self.assertAlmostEqual(hi, lo)

    def test_seed_metrics_not_probability_ensemble(self):
        f = frame(); f["other_seed"] = f.baseline
        r = evaluate(f, ["prediction", "other_seed"], "baseline", repeats=3, blocks=(1,))
        self.assertAlmostEqual(r["equal_fold_model_AP"], np.mean(list(r["equal_fold_AP_by_seed"].values())))
        self.assertEqual(len(r["equal_fold_AP_by_seed"]), 2)

    def test_multiple_baseline_seeds_are_metric_averages(self):
        f = frame(); f["base_other"] = f.prediction
        r = evaluate(f, ["prediction"], ["baseline", "base_other"], repeats=3, blocks=(1,))
        expected = np.mean(list(r["equal_fold_baseline_AP_by_seed"].values()))
        self.assertAlmostEqual(r["equal_fold_baseline_AP"], expected)
        self.assertAlmostEqual(r["CI"][0]["delta_AP_CI95"][0], r["equal_fold_delta_AP"])

    def test_daily_ties_use_stock_code_and_single_class(self):
        f = frame().iloc[:4].copy(); f["prediction"] = .5
        r = metrics(f, "prediction")
        self.assertEqual(r["daily_top10_precision"], 1)
        f["label"] = 0
        r = metrics(f, "prediction")
        self.assertIsNone(r["ROC_AUC"]); self.assertIsNone(r["daily_top10_recall"])

    def test_missing_fold_rejects_multi_year_pooling(self):
        with self.assertRaisesRegex(ValueError, "fold"):
            paired_ci(frame().drop(columns="fold"), "prediction", "baseline", repeats=2)

    def test_block_weights_match_duplicated_rows(self):
        f = frame().iloc[:4]
        weights = np.array([2, 2, 1, 1])
        repeated = f.iloc[np.repeat(np.arange(4), weights)]
        self.assertAlmostEqual(average_precision_score(f.label, f.prediction, sample_weight=weights),
                               average_precision_score(repeated.label, repeated.prediction))


if __name__ == "__main__":
    unittest.main()
