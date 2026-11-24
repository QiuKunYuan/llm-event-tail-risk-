"""Synthetic regression tests for independent labels, shared splits and rank evidence."""
import copy
import csv
import gzip
import json
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from collections import Counter
from audit_shareholder_rank_duplicates import FIELDS, profile_basis, profile_group
from build_fold_index import assignment, certified_calendar, effective_folds
from validate_tail_labels import compare_panel, reference_labels
from validation_io import REPO, new_private_directory
from verify_fold_index import verify


def synthetic_history():
    days = []
    current = date(2017, 1, 2)
    while len(days) < 900:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    rng = np.random.default_rng(42)
    factors = {day: tuple(rng.normal(0, .01, 3)) for day in days}
    rows = []
    for i, day in enumerate(days):
        if 550 <= i <= 554:
            continue
        ret = .0002 + np.asarray(factors[day]) @ np.array([.8, -.2, .3]) + rng.normal(0, .02)
        rows.append({"date": day, "return": str(ret) if i != 620 else "",
                     "risk_free_rate": ".0001" if i != 650 else ""})
    factors[days[700]] = (float("nan"), .01, .02)
    return days, factors, rows


class LabelReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days, cls.factors, cls.rows = synthetic_history()
        cls.reference, cls.metrics = reference_labels(cls.days, cls.factors, cls.rows)

    def test_svd_reference_matches_cpp_with_missing_fields_and_absent_days(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            executable = root / "labels"
            subprocess.run(["g++", "-O2", "-std=c++17", str(REPO / "scripts/build_tail_risk_labels.cpp"), "-o", str(executable)], check=True, capture_output=True)
            with (root / "returns.csv").open("w", newline="") as f:
                w = csv.writer(f)
                for row in self.rows:
                    w.writerow(["C", "000001", "000001", row["date"], row["return"], "", row["risk_free_rate"], "", "", "", ""])
            with (root / "factors.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerows((d, *self.factors[d]) for d in self.days)
            target = root / "output.csv"
            subprocess.run([str(executable), str(root / "returns.csv"), str(root / "factors.csv"), str(target)], check=True, capture_output=True)
            result = compare_panel(target, {"000001": self.reference})
            self.assertTrue(result["passed"], result)
            self.assertGreater(result["counts"]["sample_panel_rows"], 100)

    def test_missing_future_return_rf_or_factor_rejects_label(self):
        for missing in (620, 650, 700):
            for offset in range(1, 6):
                self.assertNotIn(self.days[missing - offset], self.reference)

    def test_missing_origin_can_keep_label_and_recent_missing_history_is_counted(self):
        self.assertIn(self.days[620], self.reference)
        self.assertIn(self.days[650], self.reference)
        self.assertGreater(self.metrics["boundary_counts"]["labels_with_missing_origin_observation"], 0)
        self.assertGreater(self.metrics["boundary_counts"]["labels_after_recent_missing_or_absent_observation"], 0)

    def test_absent_future_market_day_is_not_next_five_stock_rows(self):
        self.assertNotIn(self.days[549], self.reference)
        self.assertNotIn(self.days[550], self.reference)

    def test_future_change_alters_outcome_not_historical_threshold(self):
        altered = copy.deepcopy(self.rows)
        for r in altered:
            if r["date"] == self.days[731]:
                r["return"] = str(float(r["return"]) + .5)
        ref, _ = reference_labels(self.days, self.factors, altered)
        before, after = self.reference[self.days[730]], ref[self.days[730]]
        self.assertAlmostEqual(after["forward_5d_residual"] - before["forward_5d_residual"], .5)
        for k in ("threshold_p10", "threshold_p05"):
            self.assertEqual(before[k], after[k])

    def test_outcome_beyond_horizon_cannot_change_current_label(self):
        altered = copy.deepcopy(self.rows)
        for r in altered:
            if r["date"] == self.days[850]:
                r["return"] = "-9"
        ref, _ = reference_labels(self.days, self.factors, altered)
        self.assertEqual(self.reference[self.days[730]], ref[self.days[730]])

    def test_panel_comparison_rejects_missing_extra_and_duplicate_keys(self):
        day, expected = next(iter(self.reference.items()))
        with tempfile.TemporaryDirectory() as name:
            p = Path(name) / "panel.csv"
            fields = ["stock_code", "date", *expected]
            with p.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerow({"stock_code": "000001", "date": day, **expected})
                w.writerow({"stock_code": "000001", "date": day, **expected})
                w.writerow({"stock_code": "000001", "date": "2029-01-01", **expected})
            result = compare_panel(p, {"000001": self.reference})
            self.assertFalse(result["passed"])
            self.assertEqual(result["counts"]["duplicate_sample_keys"], 1)
            self.assertEqual(result["counts"]["panel_only_keys"], 1)
            self.assertGreater(result["counts"]["reference_only_keys"], 0)


class FoldIndexTests(unittest.TestCase):
    def setUp(self):
        self.fold = {"name": "2021", "train_end": "2019-12-31", "validation_start": "2020-01-02",
                     "validation_end": "2020-12-31", "test_start": "2021-01-04", "test_end": "2021-12-31"}

    def test_purge_strict_end_and_out_of_range_are_distinct(self):
        f = self.fold
        self.assertEqual(assignment("2019-12-24", "2019-12-31", f, "2017-01-01"), "train")
        self.assertEqual(assignment("2019-12-25", "2020-01-02", f, "2017-01-01"), "purged")
        self.assertEqual(assignment("2020-12-25", "2021-01-04", f, "2017-01-01"), "purged")
        self.assertEqual(assignment("2016-12-01", "2016-12-08", f, "2017-01-01"), "out_of_range")
        self.assertEqual(assignment("2022-01-04", "2022-01-11", f, "2017-01-01"), "out_of_range")

    def test_test_horizon_may_end_after_test_origin_cutoff(self):
        self.assertEqual(assignment("2021-12-31", "2022-01-10", self.fold, "2017-01-01"), "test")

    def test_calendar_rejects_uncertified_duplicate_or_non_iso_dates(self):
        for sessions, passed in [(["2020-01-02"], False), (["2020-01-02"] * 2, True), (["2020-1-2"], True)]:
            with self.assertRaises(ValueError):
                certified_calendar({"sessions": sessions, "audit": {"calendar_equal_in_observed_range": passed}})

    def test_effective_starts_use_first_actual_market_session(self):
        f = dict(self.fold, validation_start="2020-01-01", test_start="2021-01-01")
        got = effective_folds({"folds": [f]}, ["2020-01-02", "2021-01-04"], "2017-01-01")[0]
        self.assertEqual(got["validation_start"], "2020-01-02")
        self.assertEqual(got["test_start"], "2021-01-04")

    def test_readback_rejects_tampered_label_horizon_or_split(self):
        sessions = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08", "2021-01-11"]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            original = {"stock_code": "000001", "date": sessions[0], "label_p10": "1", "label_p05": "0"}
            with (root / "label.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(original)); w.writeheader(); w.writerow(original)
            row = {**original, "index_id": "0", "outcome_end": sessions[-1],
                   "outcome_end_ordinal": str(date.fromisoformat(sessions[-1]).toordinal()), "split_2021": "test"}
            def write_index(r):
                with gzip.open(root / "index.csv.gz", "wt", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(r)); w.writeheader(); w.writerow(r)
            write_index(row)
            self.assertEqual(verify(root / "label.csv", root / "index.csv.gz", sessions, [self.fold], "2017-01-01")[0], 1)
            for k, v in (("label_p05", "1"), ("outcome_end", sessions[4]), ("split_2021", "validation")):
                write_index({**row, k: v})
                with self.assertRaises(ValueError):
                    verify(root / "label.csv", root / "index.csv.gz", sessions, [self.fold], "2017-01-01")

    def test_private_products_reject_repository_and_existing_directory(self):
        with self.assertRaises(ValueError):
            new_private_directory(REPO / "reports" / "should-not-exist")
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(FileExistsError):
                new_private_directory(name)


class ShareholderEvidenceTests(unittest.TestCase):
    def test_fraction_and_percent_hypotheses_are_not_silently_rescaled(self):
        for value, scale in (("0.2", "fraction_scale_1e_6"), ("20", "percent_scale_1e_4pp")):
            counts = Counter()
            profile_basis({"holding_shares": "20", "total_shares": "100", "float_shares": "100", "pct_total": value, "pct_float": value}, counts)
            self.assertEqual(counts["total_basis_matches_" + scale], 1)
            self.assertEqual(sum(v for k, v in counts.items() if k.startswith("total_basis_matches_")), 1)

    def test_exact_repeat_and_identity_conflict_are_separate_evidence(self):
        row = {f: "same" for f in FIELDS}
        exact, changes = profile_group([(1, row), (1, dict(row))])
        self.assertEqual(exact["duplicate_rank_units_exact_repeats_in_retained_fields"], 1)
        self.assertFalse(any(changes.values()))
        other = dict(row, holder_name="different", holder_series="2", update_date="later")
        conflict, changes = profile_group([(1, row), (1, other)])
        self.assertEqual(conflict["groups_with_identity_field_conflict_at_same_rank"], 1)
        self.assertEqual(changes["holder_name"], 1)
        self.assertEqual(conflict["duplicate_groups_with_unique_ranks_after_splitting_holder_series"], 1)

    def test_twenty_rows_or_unique_series_does_not_certify_list(self):
        records = []
        for rank in range(1, 11):
            for version in range(2):
                row = {f: "" for f in FIELDS}
                row.update(holder_name=str(rank), holder_series=str(rank + 10 * version))
                records.append((rank, row))
        counts, _ = profile_group(records)
        self.assertEqual(counts["groups_exactly_20_rows_two_at_each_rank_1_to_10"], 1)
        self.assertEqual(counts["duplicate_rank_units_missing_any_id"], 10)
        self.assertNotIn("complete", counts)
        self.assertNotIn("main_graph_ready", counts)


if __name__ == "__main__":
    unittest.main()
