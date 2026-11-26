"""Synthetic-only multiclass baseline and TRAIN-only preprocessing regression tests."""
import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import joblib
import numpy as np
import pandas as pd

from probability_calibration import BIN_COLUMNS
from risk_batch_contract import RiskBatchContract
from run_loss_baselines import TrainOnlyTransform, classifier_probabilities, run_synthetic_baselines, search_configurations, tabular_day, tabular_view
from train_research_model import create_synthetic_manifest
from validation_io import sha256


class LossBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="loss-baseline-synthetic-", dir="/tmp")
        self.root = Path(self.tmp.name)
        self.contract = RiskBatchContract(create_synthetic_manifest(self.root / "inputs"))

    def tearDown(self):
        self.tmp.cleanup()  # Generated synthetic fixtures only.

    def test_imputation_scaling_body_pca_fit_train_only_and_remain_frozen(self):
        x, rows, names, body = tabular_view(self.contract)
        train = np.asarray([r["split"] == "train" and r["supervised"] for r in rows])
        train_rows = [r for r, selected in zip(rows, train) if selected]
        transform = TrainOnlyTransform(names, body, 4).fit(x[train], train_rows)
        statistics = transform.imputer.statistics_.copy(); means = transform.scaler.mean_.copy()
        pca_mean = transform.pca.mean_.copy(); components = transform.pca.components_.copy()
        heldout = x[~train].copy(); heldout[np.isfinite(heldout)] += 1e6
        result = transform.transform(heldout, names)
        self.assertTrue(np.isfinite(result).all().all())
        np.testing.assert_array_equal(statistics, transform.imputer.statistics_)
        np.testing.assert_array_equal(means, transform.scaler.mean_)
        np.testing.assert_array_equal(pca_mean, transform.pca.mean_)
        np.testing.assert_array_equal(components, transform.pca.components_)
        self.assertEqual(transform.pca.n_samples_, len(train_rows))
        self.assertEqual(transform.metadata()["fit_role"], "train")
        self.assertEqual(transform.metadata()["fit_rows"], 18)
        with self.assertRaisesRegex(ValueError, "TRAIN"):
            TrainOnlyTransform(names, body, 4).fit(x[~train], [r for r, selected in zip(rows, ~train) if selected])
        with self.assertRaisesRegex(ValueError, "order"):
            transform.transform(x, list(reversed(names)))

    def test_no_future_labels_or_keys_in_features_and_neighbor_events_explicit(self):
        b, strings = self.contract.load(self.contract.items[0])
        x, names, body = tabular_day(b, strings, self.contract.manifest["feature_order"])
        altered = {key: value.clone() for key, value in b.items()}
        altered["label_bin"].fill_(3); altered["supervised"].zero_()
        altered["outcome_end_ordinal"].fill_(999999); altered["observed_outcome_end_ordinal"].fill_(999999)
        other, order, _ = tabular_day(altered, strings, self.contract.manifest["feature_order"])
        np.testing.assert_array_equal(x, other)
        self.assertEqual(names, order)
        self.assertFalse(any("label" in name or "outcome" in name or "stock_code" in name for name in names))
        altered = {key: value.clone() for key, value in b.items()}; altered["events"][1] += 5
        other, _, _ = tabular_day(altered, strings, self.contract.manifest["feature_order"])
        peer_body = [i for i in body if names[i].startswith("peer_")]
        self.assertFalse(np.allclose(x[0, peer_body], other[0, peer_body]))

    def test_body_is_not_erased_by_invalid_json(self):
        b, strings = self.contract.load(self.contract.items[0])
        first, names, body = tabular_day(b, strings, self.contract.manifest["feature_order"])
        b["event_json_valid"].zero_(); b["field_mask"].zero_()
        second, _, _ = tabular_day(b, strings, self.contract.manifest["feature_order"])
        np.testing.assert_array_equal(first[:, body], second[:, body])
        structured = [i for i, name in enumerate(names) if name.startswith("predicted_") and "value" in name]
        self.assertTrue(np.isnan(second[:, structured]).all())

    def test_budget_and_real_candidate_optimization_rejected_before_outputs(self):
        configs = search_configurations(("logit", "lightgbm"))
        self.assertEqual(len(configs), 8)
        with self.assertRaisesRegex(ValueError, "eight"):
            run_synthetic_baselines(self.contract, self.root / "too_many", configurations=configs + configs[:1])
        self.assertFalse((self.root / "too_many").exists())
        candidate = SimpleNamespace(manifest={"scope": "candidate_panel_not_certified"})
        with self.assertRaisesRegex(ValueError, "blocked"):
            run_synthetic_baselines(candidate, self.root / "real_must_not_train")
        self.assertFalse((self.root / "real_must_not_train").exists())

    def test_missing_four_bin_class_not_mapped_to_fake_zero_probability(self):
        model = SimpleNamespace(classes_=np.asarray([0, 1]), predict_proba=lambda x: np.ones((len(x), 2)) * .5)
        with self.assertRaisesRegex(ValueError, "all classes"):
            classifier_probabilities(model, np.ones((2, 1)))

    def test_wholly_unknown_test_outcomes_still_export_predictions(self):
        manifest_path = self.contract.path
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["batches"]:
            if item["split"] != "test":
                continue
            path = manifest_path.parent / item["path"]
            with np.load(path, allow_pickle=False) as shard:
                arrays = {key: shard[key].copy() for key in shard.files}
            pred = arrays["prediction_member"]
            arrays["label_bin"][pred] = -1; arrays["supervised"][pred] = False; arrays["supervised_candidate"][pred] = False
            arrays["observed_outcome_end_ordinal"][pred] = 0
            arrays["outcome_status"][pred] = "administrative_truncation"
            arrays["unknown_reason"][pred] = "administrative_truncation"
            np.savez_compressed(path, **arrays); item["sha256"] = sha256(path)
        index_path = manifest_path.parent / manifest["prediction_index"]["path"]
        with index_path.open(newline="") as stream:
            records = list(csv.DictReader(stream))
        for row in records:
            if row["split"] == "test":
                row.update(label_bin="-1", supervised="0", supervised_candidate="0", observed_outcome_end_ordinal="0",
                           outcome_status="administrative_truncation", unknown_reason="administrative_truncation")
        with index_path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)
        manifest["prediction_index"]["sha256"] = sha256(index_path); manifest_path.write_text(json.dumps(manifest))
        contract = RiskBatchContract(manifest_path)
        report = run_synthetic_baselines(contract, self.root / "all_unknown", families=("logit",),
                    configurations=[search_configurations(("logit",))[0]])
        self.assertEqual(report["supervised_test_rows"], 0)
        self.assertEqual(report["prediction_universe_test_rows"], 18)
        frame = pd.read_csv(self.root / "all_unknown" / "test_predictions.csv")
        self.assertTrue(frame.label_bin.isna().all())
        evaluation = report["families"]["logit"]["synthetic_evaluation"]
        metrics = evaluation["folds"][0]["models"]["model"]["targets"]["p_loss_5pct"]
        self.assertIsNone(metrics["AP"])
        self.assertEqual(metrics["actual_positive_fraction_bounds"], [0., 1.])

    def test_multiclass_logit_lightgbm_selection_calibration_and_unknown_export(self):
        report = run_synthetic_baselines(self.contract, self.root / "outputs", seed=7)
        self.assertEqual(report["search_budget"], 8)
        self.assertFalse(report["legacy_residual_AP_reused"]); self.assertFalse(report["formal_training_allowed"])
        self.assertEqual(set(report["families"]), {"logit", "lightgbm"})
        self.assertEqual(report["prediction_universe_test_rows"], 18)
        self.assertEqual(report["supervised_test_rows"], 15)
        frame = pd.read_csv(self.root / "outputs" / "test_predictions.csv")
        self.assertEqual(int(frame.label_bin.isna().sum()), 3)
        for family in ("logit", "lightgbm"):
            p = frame[[family + "_" + name for name in BIN_COLUMNS]].to_numpy()
            np.testing.assert_allclose(p.sum(1), 1.)
            self.assertTrue(np.isfinite(p).all()); self.assertTrue((p >= 0).all())
            self.assertTrue((frame[family + "_p_loss_10pct"] <= frame[family + "_p_loss_5pct"] + 1e-12).all())
            self.assertTrue((frame[family + "_p_loss_5pct"] <= frame[family + "_p_loss"] + 1e-12).all())
            details = report["families"][family]
            self.assertEqual(details["preprocessing"]["fit_role"], "train")
            self.assertEqual(details["preprocessing"]["fit_rows"], 18)
            self.assertLess(details["preprocessing"]["fit_date_end"], "2020-01-01")
            self.assertEqual(details["calibration"]["split_role"], "calibration")
            self.assertFalse(details["calibration"]["calibration_quality_verified"])
            self.assertFalse(details["calibration"]["raw_age_bound_applies_after_temperature"])
            self.assertTrue(all(t["split_role"] == "calibration" and t["criterion"] == "F2" for t in details["thresholds"].values()))
            artifact = joblib.load(self.root / "outputs" / (family + "_synthetic_model.joblib"))
            np.testing.assert_array_equal(artifact["model"].classes_, np.arange(4))
            self.assertEqual(artifact["preprocessor"]["metadata"]["fit_keys_sha256"], details["preprocessing"]["fit_keys_sha256"])
            self.assertEqual(artifact["identity"], self.contract.identity)
        self.contract.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
