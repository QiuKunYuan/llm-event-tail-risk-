"""Toy-source tests for real-file adapter contracts; no financial model training."""
import contextlib
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from build_candidate_price_batches import build_candidate_batches
from build_loss_panel import build_panel
from build_loss_index import build_index
from risk_batch_contract import RiskBatchContract
from train_research_model import train_synthetic
from validation_io import sha256


class CandidatePriceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="candidate-price-fixture-", dir="/tmp")
        self.root = Path(self.tmp.name)
        self.sessions = pd.bdate_range("2019-01-02", "2021-01-15").strftime("%Y-%m-%d").tolist()
        self.returns = self.root / "toy_corrected_returns.csv.gz"
        with gzip.open(self.returns, "wt", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["stock_code", "date", "return", "market_return_float", "market_return_total", "industry_sector"])
            writer.writeheader()
            for day in self.sessions:
                for code in ("000001", "000002", "000003"):
                    if code == "000002" and day < "2021-01-04":
                        continue
                    # A short-history security and unknown outcomes must remain prediction members.
                    value = "" if code == "000002" and day >= "2021-01-05" else ".01"
                    if code == "000001" and day > "2021-01-06":
                        value = ".7"  # Future values cannot enter the Jan-6 history.
                    writer.writerow({"stock_code": code, "date": day, "return": value,
                                     "market_return_float": ".003", "market_return_total": ".006", "industry_sector": "LATEST_NOT_PIT"})
        self.calendar = self.root / "toy_calendar.json"
        self.calendar.write_text(json.dumps({"sessions": self.sessions, "audit": {"calendar_equal_in_observed_range": True}}))
        protocol = self.root / "toy_protocol.json"
        protocol.write_text(json.dumps({"folds": [{"name": "2021", "train_end": "2019-12-31",
            "selection_start": "2020-01-01", "selection_end": "2020-08-31", "calibration_start": "2020-09-01",
            "calibration_end": "2020-12-31", "test_start": "2021-01-01", "test_end": "2021-01-08"}]}))
        with contextlib.redirect_stdout(io.StringIO()):
            build_panel(self.returns, self.calendar, self.root / "panel", research_start="2019-01-02", research_end="2021-01-08")
            build_index(self.root / "panel", self.calendar, self.root / "index", protocol)
        self.args = {"returns": self.returns, "calendar": self.calendar,
            "panel_report": self.root / "panel" / "panel_report.json", "index_report": self.root / "index" / "index_report.json",
            "views_report": self.root / "index" / "index_views_report.json", "index_view": self.root / "index" / "actual_loss_index_view_2021.csv.gz",
            "fold": "2021", "dates": ["2021-01-06"], "stocks": ["000001", "000002"], "output_dir": self.root / "batches"}

    def tearDown(self):
        self.tmp.cleanup()  # Generated synthetic fixtures only; no licensed/raw source removal.

    def test_price_windows_unknown_short_history_no_sector_backfill_and_forward(self):
        report = build_candidate_batches(**self.args, market_candidate_context=True, forward_cpu=True)
        self.assertTrue(report["validation_passed"])
        self.assertFalse(report["model_training_performed"]); self.assertFalse(report["real_risk_scores_emitted"])
        self.assertEqual(report["supervised_certified_rows"], 0)
        self.assertEqual(report["rows"], 2)
        contract = RiskBatchContract(self.root / "batches" / "manifest.json")
        b, strings = contract.load(contract.items[0])
        self.assertEqual(tuple(b["seq"].shape), (2, 60, 1))
        np.testing.assert_allclose(b["seq"][0, :, 0], .01)
        self.assertEqual(int(b["seq_mask"][0].sum()), 60)
        self.assertLess(int(b["seq_mask"][1].sum()), 60)
        self.assertTrue(b["prediction_member"].all()); self.assertFalse(b["supervised"].any())
        self.assertFalse(b["supervised_certified"].any())
        self.assertEqual(int(b["label_bin"][1]), -1)
        self.assertEqual(int(b["observed_outcome_end_ordinal"][1]), 0)
        self.assertEqual(strings["sector_key"].tolist(), ["UNKNOWN"])
        self.assertFalse(b["sector_mask"].any()); self.assertFalse(b["sector_mapping_available_ordinal"].any())
        self.assertEqual(tuple(b["events"].shape), (2, 0, 1)); self.assertEqual(tuple(b["edges"].shape), (2, 0))
        np.testing.assert_allclose(b["market_seq"][0, :, 0], .003)
        self.assertTrue(b["market_mask"].all())
        self.assertEqual(report["forward"][0]["output_shape"], [2, 4])
        with self.assertRaisesRegex(ValueError, "blocked"):
            train_synthetic(contract, self.root / "forbidden_training")
        contract.assert_unchanged()

    def test_default_market_mask_and_explicit_float_source_audit(self):
        build_candidate_batches(**self.args)
        contract = RiskBatchContract(self.root / "batches" / "manifest.json")
        b, _ = contract.load(contract.items[0])
        self.assertFalse(b["market_mask"].any())
        audit = json.loads((self.root / "batches" / "market_context_audit.json").read_text())
        self.assertEqual(audit["source_column"], "market_return_float")
        self.assertFalse(audit["market_identity_certified"]); self.assertFalse(audit["return_units_certified"])
        self.assertFalse(audit["proxy_constructed"])
        self.assertTrue(audit["history_date_records"]["2021-01-06"]["date_consistent"])

    def test_market_conflict_masks_without_averaging(self):
        # Modify the toy source and regenerate the audited toy panel/index chain.
        with gzip.open(self.returns, "rt", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            if row["stock_code"] == "000003" and row["date"] == "2021-01-05":
                row["market_return_float"] = ".9"
        changed = self.root / "toy_conflicting_returns.csv.gz"
        with gzip.open(changed, "wt", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        with contextlib.redirect_stdout(io.StringIO()):
            build_panel(changed, self.calendar, self.root / "panel_conflict", research_start="2019-01-02", research_end="2021-01-08")
            build_index(self.root / "panel_conflict", self.calendar, self.root / "index_conflict", self.root / "toy_protocol.json")
        args = {**self.args, "returns": changed, "panel_report": self.root / "panel_conflict" / "panel_report.json",
            "index_report": self.root / "index_conflict" / "index_report.json", "views_report": self.root / "index_conflict" / "index_views_report.json",
            "index_view": self.root / "index_conflict" / "actual_loss_index_view_2021.csv.gz"}
        report = build_candidate_batches(**args, market_candidate_context=True)
        contract = RiskBatchContract(self.root / "batches" / "manifest.json")
        b, _ = contract.load(contract.items[0])
        self.assertEqual(report["market_date_conflicts"], 1)
        self.assertFalse(bool(b["market_mask"][0, -2, 0])); self.assertEqual(float(b["market_seq"][0, -2, 0]), 0.)

    def test_unavailable_cache_cannot_claim_frozen_model(self):
        build_candidate_batches(**self.args)
        path = self.root / "batches" / "unavailable_event_cache.json"
        cache = json.loads(path.read_text()); cache["model_sha256"] = "a" * 64
        path.write_text(json.dumps(cache))
        manifest_path = self.root / "batches" / "manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["frozen_event_cache"]["sha256"] = sha256(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Unavailable event cache"):
            RiskBatchContract(manifest_path)

    def test_future_date_absent_stock_purged_and_growing_view_rejected(self):
        cases = [({"dates": ["2026-01-01"]}, "market session"), ({"stocks": ["999999"]}, "absent"),
                 ({"dates": ["2020-12-31"], "stocks": ["000001"]}, "purged")]
        for replacement, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                build_candidate_batches(**{**self.args, **replacement})
        with self.assertRaises(FileNotFoundError):
            build_candidate_batches(**{**self.args, "views_report": self.root / "not_finished_report.json"})

    def test_source_hash_mismatch_rejected_before_outputs(self):
        with self.returns.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            build_candidate_batches(**self.args)
        self.assertFalse((self.root / "batches").exists())


if __name__ == "__main__":
    unittest.main()
