"""Equal-fold/seed evaluation, unknown coverage and actual moving blocks."""
import unittest
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from evaluate_predictions import evaluate, evaluate_risk, metrics, moving_block_dates, paired_ci
from probability_calibration import BIN_COLUMNS, PROBABILITY_COLUMNS


def frame():
    return pd.DataFrame({"stock_code": ["000001", "000002", "000003", "000004"] * 2,
                         "date": ["2021-01-04"] * 4 + ["2022-01-04"] * 4, "fold": ["2021"] * 4 + ["2022"] * 4,
                         "label": [1, 0, 0, 0, 0, 1, 1, 1], "prediction": [.9, .8, .7, .6] * 2,
                         "baseline": [.6, .7, .8, .9] * 2})


def risk_frame():
    f = pd.DataFrame({"stock_code": [f"{i:06d}" for i in range(10)], "date": ["2021-01-04"] * 10,
                      "fold": ["2021"] * 10, "industry": ["historical-A"] * 5 + ["historical-B"] * 5,
                      "label_bin": [np.nan, 2, 0, 0, 1, 3, 0, 1, np.nan, 0],
                      "outcome_status": ["unknown", "known", "known", "known", "known", "known", "known", "known", "unknown", "known"]})
    p5 = np.asarray([.99, .9, .8, .7, .6, .5, .4, .3, .2, .1])
    p = np.stack((1 - p5, np.zeros(10), p5 * .6, p5 * .4), -1)
    for j, name in enumerate(BIN_COLUMNS):
        f[name] = p[:, j]
        f["base_" + name] = np.roll(p[:, j], 1)
    return f


class EvaluationTests(unittest.TestCase):
    def test_equal_fold_not_pooled_ap(self):
        f = frame(); r = evaluate(f, ["prediction"], "baseline", repeats=4, blocks=(1,))
        expected = np.mean([average_precision_score(g.label, g.prediction) for _, g in f.groupby("fold")])
        self.assertAlmostEqual(r["equal_fold_model_AP"], expected)
        self.assertNotAlmostEqual(r["pooled_AP_by_seed"]["prediction"], expected)
        self.assertEqual(r["CI"][0]["valid_repeats"], 4)
        self.assertAlmostEqual(r["CI"][0]["delta_AP_CI95"][0], r["equal_fold_delta_AP"])

    def test_seed_metrics_not_probability_ensemble(self):
        f = frame(); f["other_seed"] = f.baseline; f["base_other"] = f.prediction
        r = evaluate(f, ["prediction", "other_seed"], ["baseline", "base_other"], repeats=3, blocks=(1,))
        self.assertAlmostEqual(r["equal_fold_model_AP"], np.mean(list(r["equal_fold_AP_by_seed"].values())))
        self.assertAlmostEqual(r["equal_fold_baseline_AP"], np.mean(list(r["equal_fold_baseline_AP_by_seed"].values())))

    def test_daily_ties_and_single_class(self):
        f = frame().iloc[:4].copy(); f["prediction"] = .5
        self.assertEqual(metrics(f, "prediction")["daily_top10_precision"], 1)
        f["label"] = 0
        r = metrics(f, "prediction")
        self.assertIsNone(r["ROC_AUC"]); self.assertIsNone(r["daily_top10_recall"])

    def test_unknown_top10_selected_before_labels(self):
        f = risk_frame(); r = evaluate_risk(f)
        primary = r["folds"][0]["models"]["model"]["targets"]["p_loss_5pct"]
        self.assertIsNone(primary["daily_top10_precision"])
        self.assertEqual(primary["daily_top10_recall"], 0.)
        selected = primary["daily_top10_selection"][0]
        self.assertEqual(selected["selected_unknown"], 1)
        self.assertEqual(selected["selected_observable"], 0)
        self.assertEqual(selected["precision_bounds"], [0., 1.])

    def test_expected_member_fractions_and_observable_comparison(self):
        r = evaluate_risk(risk_frame())
        aggregate = r["aggregates_by_seed"]["model"]
        self.assertIn("NOT_index", aggregate["interpretation"])
        main = aggregate["market"][0]["p_loss_5pct"]
        self.assertEqual((main["universe_N"], main["observable_O"], main["known_positives_B"], main["unknown_M"]), (10, 8, 2, 2))
        self.assertEqual(main["actual_positive_fraction_bounds"], [.2, .4])
        self.assertEqual(main["observable_positive_fraction"], .25)
        self.assertAlmostEqual(main["mean_probability_universe"], .549)
        self.assertNotAlmostEqual(main["mean_probability_universe"], main["mean_probability_observable_subset"])
        self.assertEqual(len(aggregate["industry"]), 2)

    def test_absolute_threshold_unknown_alert_not_false(self):
        meta = {name: {"split_role": "calibration", "criterion": "F2", "threshold": .45} for name in PROBABILITY_COLUMNS}
        r = evaluate_risk(risk_frame(), meta)
        m = r["folds"][0]["models"]["model"]["targets"]["p_loss_5pct"]
        self.assertEqual(m["unknown_alerts"], 1)
        self.assertEqual(m["known_false_alerts"], 3)
        self.assertEqual(m["threshold_recall"], 1.)
        self.assertEqual(m["FNR"], 0.)
        self.assertGreater(m["alert_universe_count"], 1)  # Not capped at Top10%.
        with self.assertRaisesRegex(ValueError, "Vcal"):
            evaluate_risk(risk_frame(), {"p_loss_5pct": {"split_role": "test", "criterion": "F2", "threshold": .5}})

    def test_wholly_unknown_rows_remain_predictions(self):
        f = risk_frame(); f["label_bin"] = np.nan; f["outcome_status"] = "unknown"
        r = evaluate_risk(f, baseline_prefixes=["base_"], repeats=2, blocks=(1,))
        m = r["folds"][0]["models"]["model"]["targets"]["p_loss_5pct"]
        self.assertEqual(m["actual_positive_fraction_bounds"], [0., 1.])
        self.assertIsNone(m["AP"]); self.assertIsNone(m["NLL"])
        self.assertEqual(r["comparisons"]["p_loss_5pct"]["CI"], [])

    def test_moving_blocks_overlap_and_preserve_consecutive_dates(self):
        days = np.arange(12)
        sampled, starts = moving_block_dates(days, 4, np.random.default_rng(42))
        self.assertEqual(len(sampled), len(days))
        self.assertTrue(any(start % 4 != 0 for start in starts))
        for j, start in enumerate(starts):
            np.testing.assert_array_equal(sampled[j * 4:(j + 1) * 4], np.arange(start, start + 4))
        all_starts = set()
        rng = np.random.default_rng(3)
        for _ in range(100):
            _, starts = moving_block_dates(days, 4, rng); all_starts.update(starts)
        self.assertEqual(all_starts, set(range(9)))
        sample, _ = moving_block_dates(days, 60, rng)
        np.testing.assert_array_equal(sample, days)

    def test_unknown_rows_allowed_in_moving_bootstrap(self):
        f = frame(); f.loc[1, "label"] = np.nan
        result = paired_ci(f, "prediction", "baseline", repeats=3, block=20)
        self.assertEqual(result["valid_repeats"], 3)
        self.assertIn("overlapping_moving", result["method"])

    def test_missing_fold_rejects_multi_year_pooling(self):
        with self.assertRaisesRegex(ValueError, "fold"):
            paired_ci(frame().drop(columns="fold"), "prediction", "baseline", repeats=2)

    def test_invalid_bins_and_missing_probability_rejected(self):
        f = risk_frame(); f.loc[0, "prob_bin_0"] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            evaluate_risk(f)
        f = risk_frame(); f["prob_bin_0"] += .01
        with self.assertRaisesRegex(ValueError, "sum to one"):
            evaluate_risk(f)


if __name__ == "__main__":
    unittest.main()
