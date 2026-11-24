"""Synthetic regressions for namespace priority and non-destructive panel checks."""
import csv
import gzip
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import prepare_resset
from audit_source_mapping import candidate_status, compare_panels, observe_mapping
from annotation_workbook_io import write_workbook


class SourceMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.spec = prepare_resset.TABLES[0]

    def tearDown(self):
        self.tmp.cleanup()

    def panel(self, name, rows):
        path = self.root / name
        with gzip.open(path, "wt", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_company_namespaces_independent_of_column_order(self):
        for headers in ({0: "公司代码()_CompanyCode", 1: "上市公司代码_Comcd"},
                        {0: "上市公司代码_Comcd", 1: "公司代码()_CompanyCode"}):
            result = prepare_resset.resolve_columns(headers, self.spec)
            self.assertEqual(headers[result["company_code"]], "上市公司代码_Comcd")
            self.assertEqual(headers[result["raw_company_code"]], "公司代码()_CompanyCode")
        result = prepare_resset.resolve_columns({0: "公司代码()_CompanyCode"}, self.spec)
        self.assertNotIn("company_code", result)

    def test_alias_priority_not_column_order(self):
        spec = prepare_resset.TableSpec("test", (), ("field",), {"field": ("preferred", "fallback")})
        result = prepare_resset.resolve_columns({0: "fallback", 9: "preferred"}, spec)
        self.assertEqual(result["field"], 9)

    def test_duplicate_alias_header_rejected(self):
        with self.assertRaises(ValueError):
            prepare_resset.resolve_columns({0: "上市公司代码_Comcd", 1: "上市公司代码_ComCd"}, self.spec)

    def test_real_converter_retains_source_and_original_field_positions(self):
        headers = ["公司代码()_CompanyCode", "上市公司代码_Comcd", "股票代码_Stkcd", "日期_Date", "日收益率_Dret", "日无风险收益率_DRfRet"]
        path = self.root / "synthetic.xlsx"
        write_workbook(path, {"data": [headers, ["999", "111", "000001", "2018-02-12", "0.01", "0.0001"]]})
        destination = io.StringIO()
        stats = {}
        with patch.object(prepare_resset, "ROOT", self.root):
            prepare_resset.convert_workbook(path, self.root / "original.zip", "folder/synthetic.xlsx", self.spec, csv.writer(destination), stats)
        values = next(csv.reader(io.StringIO(destination.getvalue())))
        row = dict(zip(self.spec.output_fields, values))
        self.assertEqual(row["company_code"], "111")
        self.assertEqual(row["raw_company_code"], "999")
        self.assertEqual(row["stock_code"], "000001")
        self.assertEqual(row["source_archive"], "original.zip")
        self.assertEqual(row["source_workbook"], "folder/synthetic.xlsx")
        self.assertEqual(row["source_row"], "2")
        self.assertEqual(self.spec.output_fields[:7], ("company_code", "stock_code", "trading_stock_code", "date", "return", "capital_return", "risk_free_rate"))

    def test_panel_allows_only_named_namespace_change(self):
        old = self.panel("old.gz", [{"company_code": "999", "stock_code": "000001", "return": "0.01"}])
        new = self.panel("new.gz", [{"company_code": "111", "stock_code": "000001", "return": "0.01"}])
        report = compare_panels(old, new, {"company_code"})
        self.assertEqual(report["changed_fields"], {"company_code": 1})
        with self.assertRaises(ValueError):
            compare_panels(old, new)
        bad = self.panel("bad.gz", [{"company_code": "111", "stock_code": "000001", "return": "0.02"}])
        with self.assertRaises(ValueError):
            compare_panels(old, bad, {"company_code"})

    def test_different_panel_length_rejected(self):
        row = {"stock_code": "000001"}
        old, new = self.panel("one.gz", [row]), self.panel("two.gz", [row, row])
        with self.assertRaises(ValueError):
            compare_panels(old, new)

    def test_observed_ranges_do_not_backfill_or_certify(self):
        rows = [{"company_code": "111", "stock_code": "000001", "date": day,
                 "company_code_field": "上市公司代码_Comcd", "raw_company_code": "999"}
                for day in ("2018-02-12", "2018-02-13")]
        pairs, companies, bridges, counts = observe_mapping(self.panel("returns.gz", rows))
        self.assertEqual(counts["two_namespaces_differ"], 2)
        missing = {"company_code": "111", "stock_code": "", "publication_date": "2018-02-12"}
        self.assertEqual(candidate_status(missing, pairs, companies), "missing_stock_one_observed_candidate_NOT_backfilled")
        self.assertEqual(missing["stock_code"], "")
        known = dict(missing, stock_code="000001")
        self.assertEqual(candidate_status(known, pairs, companies), "observed_pair_candidate_NOT_certified")
        known["publication_date"] = "2018-02-11"
        self.assertEqual(candidate_status(known, pairs, companies), "outside_observed_support_NOT_listing_dates")

    def test_mixed_namespace_rejected(self):
        row = {"company_code": "999", "stock_code": "000001", "date": "2018-02-12",
               "company_code_field": "公司代码()_CompanyCode", "raw_company_code": "999"}
        with self.assertRaises(ValueError):
            observe_mapping(self.panel("mixed.gz", [row]))


if __name__ == "__main__":
    unittest.main()
