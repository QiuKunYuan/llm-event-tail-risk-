"""Synthetic CPU acceptance for actual loss, as-of candidates and immutable folds."""
from __future__ import annotations

import contextlib
import copy
import csv
import gzip
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from build_fold_index import certified_calendar
from build_loss_index import (build_index, check_panel_row, effective_loss_folds, loss_assignment)
from build_loss_panel import (PANEL_FIELDS, build_panel, candidate_rows, spool_returns)
from loss_targets import (TARGET_VERSION, approved_folds, compound_five, coverage_bounds,
                          labels_from_return, outcome_at, parse_return, source_key,
                          stable_index_id, target_identity)
from validation_io import REPO, csv_rows, new_private_directory, sha256
from verify_loss_index import (expected_split, reference_outcome, verify_index, verify_rows)


def weekdays(first, last):
    current, end = date.fromisoformat(first), date.fromisoformat(last)
    days = []
    while current <= end:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def write_csv(path, rows, fields=None):
    fields = list(rows[0]) if fields is None else fields
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def as_strings(row):
    return {key: str(value) for key, value in row.items()}


class EconomicTargetTests(unittest.TestCase):
    def setUp(self):
        # A market holiday and weekend are absent, not zero-return sessions.
        self.days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-08", "2020-01-09", "2020-01-10", "2020-01-13"]

    def observations(self, values=None):
        values = ["0"] * len(self.days) if values is None else values
        return {d: parse_return(v, i + 1) for i, (d, v) in enumerate(zip(self.days, values))}

    def test_compounding_is_not_sum_and_keeps_common_market_loss(self):
        values = list(map(Decimal, [".1", "-.1", "0", "0", "0"]))
        self.assertEqual(compound_five(values), Decimal("-.01"))
        self.assertNotEqual(compound_five(values), sum(values))
        shared = self.observations(["0", "-.02", "-.02", "-.02", "-.02", "-.02", "0"])
        result = outcome_at(self.days, 0, shared)
        self.assertEqual(result["label_loss_5pct"], 1)
        self.assertEqual(result["label_loss_10pct"], 0)

    def test_all_four_bins_exact_threshold_equality_and_nesting(self):
        examples = [(".01", 0, (0, 0, 0)), ("0", 0, (0, 0, 0)),
                    ("-.00001", 1, (1, 0, 0)), ("-.04999999999999999", 1, (1, 0, 0)),
                    ("-.05", 2, (1, 1, 0)), ("-.09999999999999999", 2, (1, 1, 0)),
                    ("-.10", 3, (1, 1, 1)), ("-1", 3, (1, 1, 1))]
        for raw, bin_id, labels in examples:
            with self.subTest(raw=raw):
                ret = compound_five([Decimal(raw), Decimal(0), Decimal(0), Decimal(0), Decimal(0)])
                result = labels_from_return(ret)
                self.assertEqual(result["loss_bin"], bin_id)
                self.assertEqual(tuple(result[k] for k in ("label_loss", "label_loss_5pct", "label_loss_10pct")), labels)
                self.assertLessEqual(result["label_loss_10pct"], result["label_loss_5pct"])
                self.assertLessEqual(result["label_loss_5pct"], result["label_loss"])

    def test_compounded_exact_thresholds_do_not_need_epsilon(self):
        for values, target, bin_id in (([".25", "-.24", "0", "0", "0"], "-.05", 2),
                                       ([".2", "-.25", "0", "0", "0"], "-.10", 3)):
            ret = compound_five(list(map(Decimal, values)))
            self.assertEqual(ret, Decimal(target))
            self.assertEqual(labels_from_return(ret)["loss_bin"], bin_id)

    def test_known_calendar_beyond_source_tail_keeps_expected_endpoint_but_unknown(self):
        obs = self.observations()
        source_end = self.days[4]
        obs = {d: v for d, v in obs.items() if d <= source_end}
        result = outcome_at(self.days, 0, obs, source_end)
        self.assertEqual(result["outcome_end"], self.days[5])
        self.assertEqual(result["observed_outcome_end"], "")
        self.assertEqual(result["administrative_truncation"], 1)
        self.assertIn("administrative_truncation", result["unknown_reason"])
        self.assertEqual(as_strings(result), reference_outcome(self.days, 0, obs, source_end))

    def test_change_after_five_market_sessions_cannot_change_endpoint(self):
        obs = self.observations()
        before = outcome_at(self.days, 0, obs)
        obs[self.days[6]] = parse_return("-1")
        self.assertEqual(before, outcome_at(self.days, 0, obs))

    def test_zero_and_minus_one_are_real_values_not_missing(self):
        self.assertEqual(parse_return("0").state, "valid")
        self.assertEqual(parse_return("-1").state, "valid")
        self.assertEqual(compound_five(list(map(Decimal, ["-1", "3", "0", "0", "0"]))), -1)
        all_zero = outcome_at(self.days, 0, self.observations())
        self.assertEqual(all_zero["forward_5d_return"], "0")
        self.assertEqual(all_zero["loss_bin"], 0)

    def test_invalid_below_minus_one_and_nonfinite_returns_are_unknown(self):
        cases = [("", "missing_return"), ("abc", "invalid_return"), ("NaN", "nonfinite_return"),
                 ("Inf", "nonfinite_return"), ("1e999", "nonfinite_return"), ("-1.0001", "below_minus_one")]
        for raw, state in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_return(raw).state, state)
                obs = self.observations()
                obs[self.days[2]] = parse_return(raw)
                result = outcome_at(self.days, 0, obs)
                self.assertEqual(result["outcome_valid_candidate"], 0)
                self.assertEqual(result["label_loss_5pct"], "")
                self.assertEqual(result["loss_bin"], "")
                self.assertIn("missing_future_return" if raw == "" else "invalid_future_return", result["unknown_reason"])
        for raw in ("NaN", "Inf", "-1.01"):
            with self.assertRaises(ValueError):
                labels_from_return(raw)
        with self.assertRaises(ValueError):
            compound_five([Decimal(0)] * 4)

    def test_overflow_in_compounded_finite_values_is_unknown(self):
        obs = self.observations(["0", "1e100", "1e100", "1e100", "1e100", "1e100", "0"])
        result = outcome_at(self.days, 0, obs)
        self.assertEqual(result["unknown_reason"], "arithmetic_invalid")
        self.assertEqual(result["label_loss"], "")

    def test_market_calendar_not_five_next_stock_rows(self):
        obs = self.observations(["0", "0", "0", "0", "0", "-.05", "0"])
        result = outcome_at(self.days, 0, obs)
        self.assertEqual(result["outcome_end"], self.days[5])
        self.assertEqual(result["label_loss_5pct"], 1)
        del obs[self.days[3]]
        # There are still five next stock rows, but the fixed market window fails.
        result = outcome_at(self.days, 0, obs)
        self.assertEqual(result["outcome_end"], self.days[5])
        self.assertEqual(result["unknown_reason"], "missing_future_observation")
        self.assertEqual(result["label_loss"], "")

    def test_missing_current_and_no_rf_factors_do_not_block_future_target(self):
        obs = self.observations()
        del obs[self.days[0]]
        result = outcome_at(self.days, 0, obs)
        self.assertEqual(result["outcome_valid_candidate"], 1)
        self.assertEqual(result["outcome_certified_total_return"], 0)

    def test_administrative_tail_is_unknown_even_with_four_zero_returns(self):
        for position in range(len(self.days) - 5, len(self.days)):
            result = outcome_at(self.days, position, self.observations())
            self.assertEqual(result["administrative_truncation"], 1)
            self.assertEqual(result["outcome_end"], "")
            self.assertEqual(result["label_loss_5pct"], "")
            self.assertEqual(result["outcome_status"], "unknown")

    def test_endpoint_is_not_drawdown_or_assumed_liquidation_price(self):
        obs = self.observations(["0", "-.5", "1", "0", "0", "0", "0"])
        result = outcome_at(self.days, 0, obs)
        self.assertEqual(Decimal(result["forward_5d_return"]), 0)
        self.assertEqual(result["label_loss_10pct"], 0)
        self.assertEqual(result["outcome_certified_total_return"], 0)
        vanished = {self.days[0]: parse_return("0")}
        result = outcome_at(self.days, 0, vanished)
        self.assertEqual(result["label_loss_10pct"], "")
        self.assertEqual(result["missing_future_observation_count"], 5)

    def test_reference_independently_matches_every_outcome_state(self):
        for raw in ("0", "-.05", "-.10", "-1", "", "NaN", "-1.1", "1e100"):
            obs = self.observations(["0", raw, raw, raw, raw, raw, "0"])
            for i in range(len(self.days)):
                self.assertEqual(as_strings(outcome_at(self.days, i, obs)), reference_outcome(self.days, i, obs))


class AsOfUniverseTests(unittest.TestCase):
    def setUp(self):
        self.days = weekdays("2020-01-02", "2020-01-31")
        self.target = "a" * 64

    def rows(self, observations):
        return list(candidate_rows(self.days, "000001", observations, self.target, self.days[0], self.days[-1], 4))

    def test_first_observed_membership_not_future_first_last_survival_span(self):
        obs = {self.days[3]: parse_return(""), self.days[5]: parse_return("0")}
        rows = self.rows(obs)
        self.assertEqual(rows[0]["date"], self.days[3])
        self.assertEqual(rows[-1]["date"], self.days[-1])
        self.assertEqual(len(rows), len(self.days) - 3)
        self.assertTrue(all(r["prediction_eligible"] == 1 for r in rows))
        self.assertTrue(all(r["universe_status"] == "OBSERVED_UNVERIFIED" for r in rows))
        self.assertTrue(all(r["universe_certified"] == 0 for r in rows))

    def test_future_observations_change_outcome_not_current_membership_or_history(self):
        obs = {d: parse_return("0", i + 1) for i, d in enumerate(self.days)}
        before = self.rows(obs)[4]
        altered = dict(obs)
        for day in self.days[5:]:
            altered.pop(day)
        after = self.rows(altered)[4]
        for k in ("stock_code", "date", "asof_first_observed_date", "prediction_eligible", "history_observation_mask", "history_return_valid_mask", "history_complete"):
            self.assertEqual(before[k], after[k])
        self.assertEqual(before["outcome_valid_candidate"], 1)
        self.assertEqual(after["outcome_valid_candidate"], 0)
        self.assertEqual(len(self.rows(obs)), len(self.rows(altered)))

    def test_missing_current_is_retained_with_separate_history_and_outcome_masks(self):
        obs = {d: parse_return("0", i + 1) for i, d in enumerate(self.days)}
        del obs[self.days[4]]
        row = self.rows(obs)[4]
        self.assertEqual(row["current_observation_present"], 0)
        self.assertEqual(row["raw_return_t"], "")
        self.assertEqual(row["source_record_ordinal"], "")
        self.assertEqual(row["history_observation_mask"], "1110")
        self.assertEqual(row["history_complete"], 0)
        self.assertEqual(row["prediction_eligible"], 1)
        self.assertEqual(row["supervised_candidate_mask"], 1)
        self.assertEqual(row["supervised_certified_mask"], 0)
        self.assertEqual(row["daily_tradability_known"], 0)
        self.assertEqual(row["daily_tradable"], "")

    def test_short_history_has_no252_or_completed_outcome_warmup(self):
        obs = {d: parse_return("0", i + 1) for i, d in enumerate(self.days)}
        first = self.rows(obs)[0]
        self.assertEqual(first["history_observation_mask"], "0001")
        self.assertEqual(first["history_complete"], 0)
        self.assertEqual(first["outcome_valid_candidate"], 1)
        self.assertEqual(first["supervised_candidate_mask"], 1)

    def test_unknown_coverage_bounds_and_empty_denominators(self):
        bounds = coverage_bounds(10, 2, 3)
        self.assertEqual((bounds["lower"], bounds["upper"]), (.2, .5))
        self.assertAlmostEqual(bounds["observed_positive_fraction"], 2 / 7)
        self.assertIsNone(coverage_bounds(5, 0, 5)["observed_positive_fraction"])
        self.assertIsNone(coverage_bounds(0, 0, 0)["lower"])
        for counts in ((10, 8, 3), (-1, 0, 0), (1.0, 0, 0)):
            with self.assertRaises(ValueError):
                coverage_bounds(*counts)

    def test_exact_source_key_and_new_immutable_target_ids(self):
        self.assertEqual(source_key("000001", self.days[0]), ("000001", self.days[0]))
        for stock, day in (("1", self.days[0]), ("000001 ", self.days[0]), ("０００００１", self.days[0]), ("000001", "2020-1-2")):
            with self.assertRaises(ValueError):
                source_key(stock, day)
        first, _ = target_identity("a" * 64, "b" * 64, self.days[0], self.days[-1], 4)
        other, _ = target_identity("c" * 64, "b" * 64, self.days[0], self.days[-1], 4)
        self.assertNotEqual(first, other)
        identity = stable_index_id(first, "000001", self.days[0])
        self.assertEqual(len(identity), 64)
        self.assertNotEqual(identity, "0")
        self.assertNotEqual(identity, stable_index_id(other, "000001", self.days[0]))
        self.assertNotEqual(identity, stable_index_id(first, "000002", self.days[0]))


class NewFoldTests(unittest.TestCase):
    def setUp(self):
        self.days = weekdays("2017-01-02", "2024-07-31")
        self.folds = effective_loss_folds(None, self.days, "2017-01-01")
        self.fold = self.folds[0]

    def test_approved_four_folds_and_selection_calibration_intervals(self):
        self.assertEqual(len(self.folds), 4)
        self.assertEqual(self.fold["train_end"], "2019-12-31")
        self.assertEqual(self.fold["selection_end"], "2020-08-31")
        self.assertEqual(self.fold["calibration_start"], "2020-09-01")
        self.assertEqual(self.fold["calibration_end"], "2020-12-31")
        self.assertEqual(self.folds[-1]["test_end"], "2024-07-24")

    def test_each_actual_outcome_end_boundary_is_strictly_purged(self):
        f = self.fold
        examples = [("2019-12-24", "2019-12-31", "train"),
                    ("2019-12-25", f["selection_start"], "purged"),
                    ("2020-08-24", "2020-08-31", "selection"),
                    ("2020-08-25", f["calibration_start"], "purged"),
                    ("2020-12-25", f["test_start"], "purged"),
                    ("2021-12-31", "2022-01-07", "test"),
                    ("2016-12-30", "2017-01-06", "out_of_range"),
                    ("2022-01-03", "2022-01-10", "out_of_range")]
        for origin, end, split in examples:
            self.assertEqual(loss_assignment(origin, end, f, "2017-01-01"), split)
            self.assertEqual(expected_split(origin, end, f, "2017-01-01"), split)
        self.assertEqual(loss_assignment("2020-06-01", "", f, "2017-01-01"), "purged")
        self.assertEqual(loss_assignment("2021-06-01", "", f, "2017-01-01"), "test")

    def test_selection_calibration_gap_is_out_of_range_not_purge(self):
        f = dict(self.fold, selection_end="2020-08-14")
        self.assertEqual(loss_assignment("2020-08-20", "2020-08-27", f, "2017-01-01"), "out_of_range")

    def test_calendar_and_legacy_or_bad_protocol_rejected(self):
        with self.assertRaises(ValueError):
            certified_calendar({"sessions": self.days, "audit": {"calendar_equal_in_observed_range": False}})
        with self.assertRaises(ValueError):
            effective_loss_folds({"folds": [{"name": "legacy", "validation_start": "2020-01-01"}]}, self.days, "2017-01-01")
        f = dict(approved_folds()[0], calibration_start="2020-08-01")
        with self.assertRaises(ValueError):
            effective_loss_folds({"folds": [f]}, self.days, "2017-01-01")
        f = dict(approved_folds()[0], name="../escape")
        with self.assertRaises(ValueError):
            effective_loss_folds({"folds": [f]}, self.days, "2017-01-01")


class StreamingPipelineTests(unittest.TestCase):
    def create_fixture(self, root):
        days = weekdays("2016-12-01", "2024-07-31")
        calendar = root / "calendar.json"
        calendar.write_text(json.dumps({"sessions": days, "audit": {"calendar_equal_in_observed_range": True}}), encoding="utf-8")
        rows = []
        for i, day in enumerate(days):
            if day == "2020-06-03":
                continue
            rows.append({"stock_code": "000001", "date": day, "return": "-.01" if i % 9 == 0 else "0"})
        for i, day in enumerate(days):
            if "2019-06-01" <= day <= "2020-06-05":
                rows.append({"stock_code": "000002", "date": day, "return": "" if i % 7 == 0 else "0"})
        # This source is intentionally date-descending, not stock/date sorted.
        rows.reverse()
        returns = root / "returns.csv.gz"
        write_csv(returns, rows)
        return returns, calendar, days

    def build_fixture(self, root, research_end="2024-07-24"):
        returns, calendar, days = self.create_fixture(root)
        with contextlib.redirect_stdout(io.StringIO()):
            panel_report = build_panel(returns, calendar, root / "panel", research_end=research_end, history_sessions=6)
            index_report = build_index(root / "panel", calendar, root / "index")
        return returns, calendar, days, panel_report, index_report

    def test_full_streaming_panel_index_and_independent_raw_readback(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            returns, calendar, days, panel_report, index_report = self.build_fixture(root)
            with contextlib.redirect_stdout(io.StringIO()):
                verification = verify_index(returns, root / "panel", root / "index", calendar, root / "verification")
            self.assertTrue(verification["passed"])
            self.assertTrue(verification["raw_source_keys_verified"])
            self.assertFalse(verification["fully_certified_listing_universe"])
            self.assertFalse(verification["fully_certified_total_return"])
            self.assertFalse(verification["formal_training_allowed"])
            self.assertEqual(verification["rows"], panel_report["prediction_candidates"])
            self.assertGreater(panel_report["unknown_outcomes"], 100)
            self.assertEqual(index_report["target_version"], TARGET_VERSION)
            panel_rows = list(csv_rows(root / "panel" / "actual_loss_candidate_panel.csv.gz"))
            missing_current = next(r for r in panel_rows if r["stock_code"] == "000001" and r["date"] == "2020-06-03")
            self.assertEqual(missing_current["prediction_eligible"], "1")
            self.assertEqual(missing_current["current_observation_present"], "0")
            self.assertEqual(missing_current["supervised_candidate_mask"], "1")
            vanished = next(r for r in panel_rows if r["stock_code"] == "000002" and r["date"] == "2024-07-24")
            self.assertEqual(vanished["outcome_status"], "unknown")
            self.assertEqual(vanished["label_loss_10pct"], "")

    def test_tail_candidates_retained_and_unlabeled(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            returns, calendar, days, panel_report, index_report = self.build_fixture(root, "2024-07-31")
            self.assertEqual(panel_report["unknown_reason_administrative_truncation"], 10)
            panel_rows = list(csv_rows(root / "panel" / "actual_loss_candidate_panel.csv.gz"))
            last = panel_rows[-1]
            self.assertEqual(last["prediction_eligible"], "1")
            self.assertEqual(last["administrative_truncation"], "1")
            self.assertEqual(last["loss_bin"], "")
            self.assertEqual(last["outcome_end"], "")

    def test_readback_rejects_tampered_labels_keys_ids_splits_and_masks(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            returns, calendar, days, panel_report, index_report = self.build_fixture(root)
            panel = root / "panel" / "actual_loss_candidate_panel.csv.gz"
            original = list(csv_rows(root / "index" / "actual_loss_company_day_index.csv.gz"))
            folds = effective_loss_folds(None, days, panel_report["research_start"])
            target = panel_report["target_version_id"]
            changes = [("stock_code", "000099"), ("index_id", "0"), ("outcome_end_ordinal", "0"),
                       ("split_2021", "test"), ("supervised_certified_2021", "1"),
                       ("history_observation_mask", "000000"), ("label_loss_5pct", "1")]
            for field, value in changes:
                with self.subTest(field=field):
                    rows = copy.deepcopy(original)
                    rows[0][field] = value
                    tampered = root / "tampered.csv.gz"
                    write_csv(tampered, rows)
                    with self.assertRaises(ValueError):
                        verify_rows(panel, tampered, days, folds, target, "2017-01-01", "2024-07-24", 6)

    def test_canonical_views_preserve_unknown_bins_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            returns, calendar, days, panel_report, index_report = self.build_fixture(root)
            view = root / "index" / "actual_loss_index_view_2021.csv.gz"
            rows = list(csv_rows(view))
            unknown = next(r for r in rows if r["outcome_status"] == "unknown")
            self.assertEqual(unknown["loss_bin"], "-1")
            self.assertEqual(unknown["prediction_member"], "1")
            self.assertEqual(unknown["supervised_candidate"], "0")
            self.assertTrue(all(r["supervised_certified"] == "0" for r in rows))
            self.assertTrue({"selection", "calibration"}.issubset({r["split"] for r in rows}))
            views_report = index_report["fold_views"]
            self.assertEqual(views_report["parent_index_sha256"], index_report["index_sha256"])
            self.assertEqual(views_report["parent_panel_sha256"], panel_report["panel_sha256"])
            self.assertEqual(views_report["views"]["2021"]["sha256"], sha256(view))
            unknown["loss_bin"] = "0"
            write_csv(view, rows)
            with self.assertRaises(ValueError):
                verify_index(returns, root / "panel", root / "index", calendar, root / "bad_view_verify")

    def test_raw_source_change_and_stale_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            returns, calendar, days, panel_report, index_report = self.build_fixture(root)
            rows = list(csv_rows(returns))
            rows[0]["return"] = ".15"
            write_csv(returns, rows)
            with self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
                verify_index(returns, root / "panel", root / "index", calendar, root / "bad_verify")
            index = root / "index" / "actual_loss_company_day_index.csv.gz"
            rows = list(csv_rows(index))
            rows.pop()
            write_csv(index, rows)
            with self.assertRaises(ValueError):
                verify_index(returns, root / "panel", root / "index", calendar, root / "bad_verify_2")

    def test_source_duplicates_and_bad_keys_rejected_global_missing_session_nullable(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            days = weekdays("2020-01-02", "2020-01-10")
            scenarios = [[{"stock_code": "000001", "date": days[0], "return": "0"}] * 2,
                         [{"stock_code": "1", "date": days[0], "return": "0"}],
                         [{"stock_code": "000001", "date": d, "return": "0"} for d in days if d != days[3]]]
            for i, rows in enumerate(scenarios):
                source = root / f"source_{i}.csv"
                write_csv(source, rows)
                if i < 2:
                    with self.assertRaises(ValueError):
                        spool_returns(source, root / f"spool_{i}.sqlite", days)
                else:
                    con, first, profile = spool_returns(source, root / f"spool_{i}.sqlite", days)
                    con.close()
                    self.assertFalse(profile["source_calendar_range_equal"])
                    self.assertEqual(profile["global_market_sessions_missing_in_source"], 1)
                    observations = {r["date"]: parse_return(r["return"]) for r in rows}
                    self.assertEqual(outcome_at(days, 0, observations)["unknown_reason"], "missing_future_observation")

    def test_private_outputs_never_overwrite_or_enter_git(self):
        with self.assertRaises(ValueError):
            new_private_directory(REPO / "outputs" / "candidate-test")
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(FileExistsError):
                new_private_directory(name)

    def test_candidate_cannot_claim_certification_or_synthesize_current_zero(self):
        days = weekdays("2020-01-02", "2020-01-31")
        observations = {d: parse_return("0", i + 1) for i, d in enumerate(days)}
        del observations[days[4]]
        row = as_strings(list(candidate_rows(days, "000001", observations, "a" * 64, days[0], days[-1], 4))[4])
        positions = {d: i for i, d in enumerate(days)}
        self.assertEqual(check_panel_row(row, "a" * 64, days, positions, 4), ("000001", days[4]))
        for field, value in (("universe_certified", "1"), ("outcome_certified_total_return", "1"),
                             ("raw_return_t", "0"), ("daily_tradable", "1"), ("label_loss_5pct", "1")):
            with self.assertRaises(ValueError):
                check_panel_row({**row, field: value}, "a" * 64, days, positions, 4)


if __name__ == "__main__":
    unittest.main()
