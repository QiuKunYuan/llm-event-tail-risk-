"""Calibration, fail-closed batch contract, unknown export and CPU resume tests."""
import csv
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch
from probability_calibration import cumulative_probabilities, fit_temperature, select_threshold, temperature_scale, validation_partition
from loss_targets import labels_from_return
from risk_batch_contract import RiskBatchContract
from train_research_model import create_synthetic_manifest, train_synthetic
from validation_io import sha256


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.p = np.asarray([[.1, .2, .3, .4], [.4, .3, .2, .1], [.8, .1, .05, .05], [.1, .1, .2, .6]])
        self.y = np.asarray([3, 0, 2, 1]); self.days = ["2020-09-07"] * 4
        self.ends = [date(2020, 9, 14).toordinal()] * 4

    def test_exact_return_boundaries_match_four_bin_probability_contract(self):
        for value, expected_bin in (("0", 0), ("-.00001", 1), ("-.049999", 1), ("-.05", 2),
                                    ("-.099999", 2), ("-.10", 3), ("-1", 3)):
            labels = labels_from_return(value)
            self.assertEqual(labels["loss_bin"], expected_bin)
            probabilities = cumulative_probabilities(np.eye(4)[[expected_bin]])
            self.assertEqual(probabilities["p_loss"][0], labels["label_loss"])
            self.assertEqual(probabilities["p_loss_5pct"][0], labels["label_loss_5pct"])
            self.assertEqual(probabilities["p_loss_10pct"][0], labels["label_loss_10pct"])

    def test_temperature_final_normalization_nesting_and_identity(self):
        for t in (.1, 1., 10.):
            p = temperature_scale(self.p, t)
            np.testing.assert_allclose(p.sum(1), 1.)
            c = cumulative_probabilities(p)
            self.assertTrue((c["p_loss_10pct"] <= c["p_loss_5pct"]).all())
            self.assertTrue((c["p_loss_5pct"] <= c["p_loss"] + 1e-14).all())
        np.testing.assert_array_equal(temperature_scale(self.p, 1.), self.p)
        # An arbitrary temperature changes nonlinear weights; raw age bound is not claimed.
        fit = fit_temperature(self.p, self.y, self.days, self.ends, 2020, "2021-01-04")
        self.assertGreater(fit["temperature"], 0)
        self.assertFalse(fit["calibration_quality_verified"])
        self.assertEqual(fit["raw_age_bound_applies_after_temperature"], fit["temperature"] == 1.)

    def test_zero_bin_not_unknown_zero_score_and_invalid_temperature(self):
        p = np.asarray([[1., 0., 0., 0.]])
        np.testing.assert_array_equal(temperature_scale(p, 2.), p)
        for t in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                temperature_scale(p, t)
        with self.assertRaisesRegex(ValueError, "sum to one"):
            temperature_scale(np.zeros((1, 4)))

    def test_validation_calendar_and_actual_endpoint_purge(self):
        self.assertEqual(validation_partition(["2020-08-31", "2020-09-01"], 2020).tolist(), ["selection", "calibration"])
        with self.assertRaisesRegex(ValueError, "Sep-Dec"):
            fit_temperature(self.p, self.y, ["2020-08-31"] * 4, self.ends, 2020, "2021-01-04")
        with self.assertRaisesRegex(ValueError, "purge"):
            fit_temperature(self.p, self.y, self.days, [date(2021, 1, 4).toordinal()] * 4, 2020, "2021-01-04")
        with self.assertRaisesRegex(ValueError, "validation year"):
            fit_temperature(self.p, self.y, ["2021-09-07"] * 4, self.ends, 2020, "2022-01-04")

    def test_f2_threshold_calibration_only_and_no_positive_undefined(self):
        p = np.asarray([.9, .8, .7, .1]); y = np.asarray([1, 0, 1, 0])
        args = dict(days=self.days, outcome_end=self.ends, validation_year=2020, test_start="2021-01-04")
        result = select_threshold(p, y, **args)
        self.assertEqual(result["threshold"], .7)
        self.assertEqual(result["split_role"], "calibration")
        self.assertTrue(result["no_all_risk_guarantee"])
        self.assertIsNone(select_threshold(p, np.zeros(4), **args)["threshold"])
        with self.assertRaisesRegex(ValueError, "criterion"):
            select_threshold(p, y, criterion="test_F2", **args)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="actual-loss-contract-", dir="/tmp")
        self.root = Path(self.tmp.name)
        self.manifest_path = create_synthetic_manifest(self.root / "inputs")
        self.manifest = json.loads(self.manifest_path.read_text())

    def tearDown(self):
        self.tmp.cleanup()  # Only generated synthetic /tmp fixtures, never raw sources.

    def _rewrite_shard(self, change, index=0):
        item = self.manifest["batches"][index]
        path = self.manifest_path.parent / item["path"]
        with np.load(path, allow_pickle=False) as shard:
            arrays = {key: shard[key].copy() for key in shard.files}
        change(arrays)
        np.savez_compressed(path, **arrays)
        item["sha256"] = sha256(path)
        self.manifest_path.write_text(json.dumps(self.manifest))

    def test_valid_daily_contract_and_indirect_context_ablation(self):
        contract = RiskBatchContract(self.manifest_path)
        b, _ = contract.load(contract.items[0])
        self.assertEqual(len(b["market_seq"]), 1)
        self.assertEqual(len(b["sector_seq"]), 2)
        self.assertEqual(int(b["prediction_member"].sum()), 6)
        ablated, _ = contract.load(contract.items[0], mode="no_market")
        self.assertFalse(ablated["seq_mask"][..., 2].any())
        self.assertFalse(ablated["market_mask"].any())
        self.assertTrue(ablated["seq_mask"][..., 0].all())
        contract.assert_unchanged()

    def test_future_per_record_not_just_manifest_max(self):
        cutoff = date.fromisoformat(self.manifest["batches"][0]["date"]).toordinal()
        self._rewrite_shard(lambda a: a["seq_available_ordinal"].__setitem__((0, 0, 0), cutoff + 1))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "per-record"):
            contract.load(contract.items[0])

    def test_context_mapping_dedup_and_future_sector_effective(self):
        self._rewrite_shard(lambda a: a["sector_mapping_effective_ordinal"].__setitem__(0, date(2026, 1, 1).toordinal()))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "effective"):
            contract.load(contract.items[0])

    def test_market_per_node_rejected_on_disk(self):
        def alter(a):
            for key in ("market_seq", "market_mask", "market_source_ordinal", "market_available_ordinal"):
                a[key] = np.repeat(a[key], 8, 0)
            a["market_index"] = np.arange(8, dtype="int64")
        self._rewrite_shard(alter)
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "once per date"):
            contract.load(contract.items[0])

    def test_unknown_expected_end_not_observed_or_supervised(self):
        contract = RiskBatchContract(self.manifest_path)
        b, strings = contract.load(contract.groups["test"][0])
        self.assertTrue(bool(b["prediction_member"][5])); self.assertFalse(bool(b["supervised"][5]))
        self.assertEqual(int(b["label_bin"][5]), -1)
        self.assertGreater(int(b["outcome_end_ordinal"][5]), 0)
        self.assertEqual(int(b["observed_outcome_end_ordinal"][5]), 0)
        self.assertEqual(strings["unknown_reason"][5], "administrative_truncation")

    def test_bad_sha_field_order_and_role_fail_closed(self):
        contract = RiskBatchContract(self.manifest_path)
        path = self.manifest_path.parent / contract.items[0]["path"]
        with path.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            contract.load(contract.items[0])
        self.manifest["fields"][0]["role"] = "future_label"
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "role"):
            RiskBatchContract(self.manifest_path)

    def test_field_order_not_manifest_only_and_labels_not_features(self):
        self.manifest["fields"].reverse(); self.manifest_path.write_text(json.dumps(self.manifest))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "field order"):
            contract.load(contract.items[0])
        self.manifest["feature_order"]["stock"][0]["name"] = "future_label"
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "not a risk input"):
            RiskBatchContract(self.manifest_path)

    def test_index_membership_duplicate_edges_and_cache_body_binding(self):
        self._rewrite_shard(lambda a: a["prediction_member"].__setitem__(5, False))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "membership"):
            contract.load(contract.items[0])

    def test_duplicate_pairs_and_rejected_disclosures(self):
        self._rewrite_shard(lambda a: a["edges"].__setitem__((slice(None), 1), a["edges"][:, 0]))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            contract.load(contract.items[0])

    def test_cache_vector_binding_catches_rehashed_shard(self):
        self._rewrite_shard(lambda a: a["events"].__setitem__((0, 0, 0), 100.))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "cache/body"):
            contract.load(contract.items[0])

    def test_predicted_fields_cache_binding_and_quality_source(self):
        self._rewrite_shard(lambda a: a["predicted_fields"].__setitem__((0, 0, 0), 100.))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "predicted fields/cache"):
            contract.load(contract.items[0])
        self.manifest["quality_sources"]["evidence_quality"] = "LLM_self_reported_confidence"
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "self-reported"):
            RiskBatchContract(self.manifest_path)

    def test_incomplete_disclosure_rejected_even_if_rehashed(self):
        self._rewrite_shard(lambda a: a["edge_accepted_complete"].__setitem__(0, False))
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "complete holdings"):
            contract.load(contract.items[0])

    def test_actual_end_cross_split_rejected(self):
        def alter(a):
            end = date(2020, 1, 6).toordinal()
            a["outcome_end_ordinal"][:6] = end; a["observed_outcome_end_ordinal"][:6] = end
        self._rewrite_shard(alter)
        contract = RiskBatchContract(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "Unpurged"):
            contract.load(contract.items[0])

    def test_canonical_candidate_view_aliases_without_certification_or_training(self):
        # Transform synthetic fixtures only, to exercise the data-team view interface.
        original = self.manifest_path.parent / self.manifest["prediction_index"]["path"]
        with original.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        source_id = "a" * 64
        for row in rows:
            row["loss_bin"] = row.pop("label_bin")
            row.pop("supervised")
            row["target_version_id"] = source_id
            if row["outcome_status"] == "known":
                row["outcome_status"] = "complete_raw_return_candidate"
        inactive = rows[0].copy(); inactive.update(date="2018-01-01", stock_code="999999", split="out_of_range")
        rows.append(inactive)
        view = self.manifest_path.parent / "candidate_index.csv"
        with view.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        for item in self.manifest["batches"]:
            path = self.manifest_path.parent / item["path"]
            with np.load(path, allow_pickle=False) as shard:
                arrays = {name: shard[name].copy() for name in shard.files}
            arrays["supervised"].fill(False)
            arrays["outcome_status"][arrays["label_bin"] >= 0] = "complete_raw_return_candidate"
            np.savez_compressed(path, **arrays); item["sha256"] = sha256(path)
        self.manifest.update(scope="candidate_panel_not_certified", source_target_version_id=source_id,
                             source_artifacts=[{"path": original.name, "sha256": sha256(original)}],
                             prediction_index={"path": view.name, "sha256": sha256(view)})
        self.manifest_path.write_text(json.dumps(self.manifest))
        contract = RiskBatchContract(self.manifest_path)
        for item in contract.items:
            b, _ = contract.load(item)
            self.assertFalse(b["supervised"].any()); self.assertFalse(b["supervised_certified"].any())
        self.assertEqual(len(contract.index), 60)
        with self.assertRaisesRegex(ValueError, "blocked"):
            train_synthetic(contract, self.root / "must_not_train")

    def test_synthetic_checkpoint_exact_resume_and_universe_export(self):
        contract = RiskBatchContract(self.manifest_path)
        full = self.root / "full"; resumed = self.root / "resumed"
        report = train_synthetic(contract, full, epochs=2, seed=13, modality_dropout=.25)
        interrupted = train_synthetic(contract, resumed, epochs=2, seed=13, modality_dropout=.25, stop_after_steps=2)
        self.assertTrue(interrupted["interrupted"])
        done = train_synthetic(contract, resumed, epochs=2, seed=13, modality_dropout=.25, resume=resumed / "checkpoint.pt")
        self.assertEqual(report["history"], done["history"])
        first = torch.load(full / "selected_model.pt", weights_only=False)
        second = torch.load(resumed / "selected_model.pt", weights_only=False)
        for key in first["model"]:
            torch.testing.assert_close(first["model"][key], second["model"][key], rtol=0, atol=0)
        self.assertEqual((full / "test_predictions.csv").read_bytes(), (resumed / "test_predictions.csv").read_bytes())
        self.assertEqual(done["prediction_universe_rows"], 18)
        self.assertEqual(done["supervised_test_rows"], 15)
        self.assertFalse(done["real_daily_assembler_implemented"])
        with self.assertRaisesRegex(ValueError, "Checkpoint"):
            train_synthetic(contract, resumed, epochs=2, seed=14, modality_dropout=.25, resume=resumed / "checkpoint.pt")


if __name__ == "__main__":
    unittest.main()
