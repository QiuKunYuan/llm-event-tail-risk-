"""Synthetic tests only: no manual gold or financial result is fabricated."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

import build_sft_dataset
from annotation_validation import (VERSION, label_value, preflight_gold, temporal_split,
                                   unique_rows, validate_membership)
from annotation_workbench import (SOURCE_COLUMNS, agreement, approve, import_book, metadata,
                                  save_annotation_book, write_csv)
from annotation_workbook_io import NS, read_workbook, write_workbook


def fixture():
    rows = []
    for i in range(6):
        day = "2018-02-12" if i < 4 else "2020-02-12" if i == 4 else "2021-02-12"
        rows.append({"record_id": f"synthetic-{i}", "family_id": f"family-{i}", "forecast_object": "1",
                     "stock_code": "000001", "publication_date": day, "report_date": day,
                     "forecast_text": f"虚构记录{i}。预计净利润同比增长30%，主要由于产品销量增加。",
                     "proposed_split": "train" if i < 4 else "validation" if i == 4 else "test"})
    main = unique_rows(rows)
    return {"main": main, "pilot": {r["record_id"]: r for r in rows[:3]},
            "double": {r["record_id"]: r for r in (rows[1], rows[3], rows[5])},
            "package_id": "synthetic-package", "hashes": {}}


def completed(original, role="A", direction="改善"):
    return {**original, "direction": direction, "causes_json": '["产品销量增加"]',
            "evidence_json": '["预计净利润同比增长30%，主要由于产品销量增加。"]',
            "annotator_confidence": "高", "annotator_note": "", "work_status": "已填写",
            "needs_adjudication": "no", "review_status": "pending", "reviewer": "",
            "annotator_role": role, "annotation_version": VERSION}


class AnnotationWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pack = fixture()

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, name="pilot_a", role="A", fill=False, mutation=None):
        rows = list(self.pack["pilot"].values())
        if role == "B":
            rows = [rows[1]]
        path = self.root / ("filled.xlsx" if fill else "blank.xlsx")
        save_annotation_book(path, rows, metadata(self.pack, name, role))
        sheets = read_workbook(path)
        if fill:
            for row in sheets["待标注"][1:]:
                row[2:12] = ["改善", "已说明", "产品销量增加", "预计净利润同比增长30%，主要由于产品销量增加。", "", "", "高", "已填写", "", "12"]
        if mutation:
            mutation(sheets)
        if fill or mutation:
            target = self.root / "edited.xlsx"
            write_workbook(target, sheets)
            return target
        return path

    def test_blank_workbook_is_unfinished_not_gold(self):
        rows, issues, report = import_book(self.book(), self.pack)
        self.assertEqual(report["counts"]["unfinished"], 3)
        self.assertEqual(report["human_gold_count"], 0)
        self.assertEqual(len(issues), 3)
        self.assertTrue(all(r["review_status"] == "pending" for r in rows))

    def test_filled_roundtrip_flat_schema_and_leading_zero(self):
        rows, issues, report = import_book(self.book(fill=True), self.pack)
        self.assertFalse(issues)
        self.assertEqual(report["counts"]["valid_draft"], 3)
        self.assertEqual(rows[0]["stock_code"], "000001")
        self.assertEqual(label_value(rows[0])["causes"], ["产品销量增加"])
        self.assertEqual(rows[0]["elapsed_seconds"], "12")
        self.assertEqual(rows[0]["review_status"], "pending")

    def test_missing_cause_not_missing_direction(self):
        def change(s):
            for row in s["待标注"][1:]:
                row[3], row[4] = "未提及", ""
        rows, issues, _ = import_book(self.book(fill=True, mutation=change), self.pack)
        self.assertFalse(issues)
        self.assertEqual(label_value(rows[0]), {"direction": "改善", "causes": [], "evidence": ["预计净利润同比增长30%，主要由于产品销量增加。"]})

    def test_newlines_make_free_phrases_without_taxonomy(self):
        def change(s):
            for row in s["待标注"][1:]:
                row[4] = "产品销量增加\n一句含；分号的自由短语"
        rows, _, _ = import_book(self.book(fill=True, mutation=change), self.pack)
        self.assertEqual(len(json.loads(rows[0]["causes_json"])), 2)

    def test_source_tampering_rejected(self):
        for index in SOURCE_COLUMNS.values():
            with self.subTest(index=index):
                path = self.root / f"source-{index}.xlsx"
                save_annotation_book(path, list(self.pack["pilot"].values()), metadata(self.pack, "pilot_a", "A"))
                sheets = read_workbook(path)
                sheets["待标注"][1][index] += "changed"
                target = self.root / f"changed-{index}.xlsx"
                write_workbook(target, sheets)
                with self.assertRaises(ValueError):
                    import_book(target, self.pack)

    def test_missing_and_duplicate_ids_rejected(self):
        for duplicate in (False, True):
            path = self.root / f"id-{duplicate}.xlsx"
            save_annotation_book(path, list(self.pack["pilot"].values()), metadata(self.pack, "pilot_a", "A"))
            sheets = read_workbook(path)
            if duplicate:
                sheets["待标注"].append(sheets["待标注"][1])
            else:
                sheets["待标注"].pop()
            target = self.root / f"id-bad-{duplicate}.xlsx"
            write_workbook(target, sheets)
            with self.assertRaises(ValueError):
                import_book(target, self.pack)

    def test_wrong_version_and_role_rejected(self):
        path = self.book(mutation=lambda s: s["包信息"].append(["version", "unknown-version"]))
        with self.assertRaises(ValueError):
            import_book(path, self.pack)

    def test_mixed_annotator_role_rejected(self):
        with self.assertRaises(ValueError):
            import_book(self.book(name="pilot_a", role="B"), self.pack)

    def test_macros_external_relationships_and_bad_cells_rejected(self):
        original = self.root / "safe.xlsx"
        write_workbook(original, {"测试": [["原文"]]})
        for kind in ("macro", "external", "cell"):
            bad = self.root / f"bad-{kind}.xlsx"
            with ZipFile(original) as source, ZipFile(bad, "x", ZIP_DEFLATED) as target:
                for name in source.namelist():
                    data = source.read(name)
                    if kind == "cell" and name == "xl/worksheets/sheet1.xml":
                        data = data.replace(b'r="A1"', b'r="ZZZZZZZZ1"')
                    target.writestr(name, data)
                if kind == "macro":
                    target.writestr("xl/vbaProject.bin", b"synthetic")
                elif kind == "external":
                    target.writestr("xl/_rels/synthetic.rels", '<Relationships><Relationship TargetMode="External" Target="https://example.invalid/"/></Relationships>')
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                read_workbook(bad)

    def test_low_confidence_needs_discussion(self):
        def change(s):
            for row in s["待标注"][1:]:
                row[8], row[10] = "低", "虚构测试：请人工核对比较期"
        rows, issues, report = import_book(self.book(fill=True, mutation=change), self.pack)
        self.assertFalse(issues)
        self.assertEqual(report["counts"]["needs_discussion"], 3)
        self.assertTrue(all(r["needs_adjudication"] == "yes" for r in rows))

    def test_cause_state_conflict_reported(self):
        rows, issues, _ = import_book(self.book(fill=True, mutation=lambda s: s["待标注"][1].__setitem__(3, "未提及")), self.pack)
        self.assertEqual(len(issues), 1)
        self.assertEqual(rows[0]["needs_adjudication"], "yes")

    def test_formula_rejected_and_literal_equals_is_text(self):
        path = self.root / "literal.xlsx"
        write_workbook(path, {"测试": [["文字"], ["=not-a-formula"]]})
        self.assertEqual(read_workbook(path)["测试"][1][0], "=not-a-formula")
        bad = self.root / "formula.xlsx"
        with ZipFile(path) as source, ZipFile(bad, "x", ZIP_DEFLATED) as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    data = data.replace(b'<is>', b'<f>1+1</f><is>', 1)
                target.writestr(name, data)
        with self.assertRaises(ValueError):
            read_workbook(bad)

    def test_shared_strings_supported(self):
        path = self.root / "shared-original.xlsx"
        write_workbook(path, {"测试": [["列"], ["000001"]]})
        target = self.root / "shared.xlsx"
        with ZipFile(path) as source, ZipFile(target, "x", ZIP_DEFLATED) as out:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    data = data.replace(b't="inlineStr"><is><t xml:space="preserve">000001</t></is>', b't="s"><v>0</v>')
                out.writestr(name, data)
            out.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}"><si><t>000001</t></si></sst>')
        self.assertEqual(read_workbook(target)["测试"][1][0], "000001")

    def test_long_source_refuses_truncation(self):
        with self.assertRaises(ValueError):
            write_workbook(self.root / "too-long.xlsx", {"源": [["x" * 32768]]})

    def test_newlines_preserved(self):
        path = self.root / "newline.xlsx"
        write_workbook(path, {"源": [["甲\r\n乙\n丙"]]})
        self.assertEqual(read_workbook(path)["源"][0][0], "甲\r\n乙\n丙")


class GoldWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.pack = fixture()
        self.a = {k: completed(v) for k, v in self.pack["pilot"].items()}
        key = list(self.a)[1]
        self.b = {key: completed(self.pack["main"][key], "B")}

    def test_membership_and_cross_split_checks(self):
        main, pilot, double = (list(self.pack[k].values()) for k in ("main", "pilot", "double"))
        validate_membership(main, pilot, double, sizes=(6, 3, 3))
        bad = copy.deepcopy(main)
        bad[-1]["family_id"] = bad[0]["family_id"]
        with self.assertRaises(ValueError):
            validate_membership(bad, pilot, double, sizes=(6, 3, 3))
        with self.assertRaises(ValueError):
            validate_membership(main, [main[-1]], double, sizes=(6, 1, 3))

    def test_evidence_count_and_whitespace_are_strict(self):
        row = dict(next(iter(self.a.values())))
        for spans in ([], ["不在正文"], ["预计净利润"] * 4):
            row["evidence_json"] = json.dumps(spans)
            with self.assertRaises(ValueError):
                label_value(row)
        row["forecast_text"] = "利润  增长\r\n主要由于销量增加"
        row["evidence_json"] = '["利润 增长"]'
        with self.assertRaises(ValueError):
            label_value(row)
        row["evidence_json"] = '["利润  增长\\n主要由于销量增加"]'
        label_value(row)

    def test_empty_reason_phrase_rejected(self):
        row = dict(next(iter(self.a.values())), causes_json='[""]')
        with self.assertRaises(ValueError):
            label_value(row)

    def test_pending_blank_or_typo_review_not_gold(self):
        for flag in ("", "true", "n0", "yes", "no"):
            row = dict(next(iter(self.a.values())), needs_adjudication=flag)
            with self.assertRaises(ValueError):
                label_value(row, require_review=True)

    def test_accepted_flag_does_not_bypass_work_status_or_version(self):
        row = dict(next(iter(self.a.values())), review_status="accepted", reviewer="合成复核者")
        label_value(row, require_review=True)
        for change in ({"work_status": "未完成"}, {"work_status": "待讨论"},
                       {"work_status": "typo"}, {"annotation_version": "old"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                label_value(dict(row, **change), require_review=True)

    def test_temporal_boundary_and_empty_independent_pairs(self):
        self.assertEqual(temporal_split("2017-02-06"), "train")
        self.assertEqual(temporal_split("2024-07-24"), "test")
        for day in ("2017-02-05", "2024-07-25", "2025-01-01"):
            with self.assertRaises(ValueError):
                temporal_split(day)
        self.b[next(iter(self.b))]["work_status"] = "未完成"
        report, _ = agreement(self.a, self.b)
        self.assertEqual(report["valid_independent_pairs"], 0)
        self.assertIsNone(report["direction_agreement"])
        self.assertIsNone(report["cohens_kappa"])

    def test_agreement_and_undefined_constant_kappa(self):
        report, differences = agreement(self.a, self.b)
        self.assertEqual(report["direction_agreement"], 1)
        self.assertIsNone(report["cohens_kappa"])
        self.assertFalse(differences)
        a, b = {}, {}
        for i, (left, right) in enumerate(zip(["改善", "恶化", "改善"], ["改善", "恶化", "恶化"])):
            row = list(self.pack["main"].values())[i]
            a[row["record_id"]], b[row["record_id"]] = completed(row, direction=left), completed(row, "B", right)
        report, differences = agreement(a, b)
        self.assertAlmostEqual(report["cohens_kappa"], .4)
        self.assertEqual(len(differences), 1)

    def test_approval_requires_full_independent_pairs(self):
        self.b[next(iter(self.b))]["work_status"] = "未完成"
        with self.assertRaises(ValueError):
            approve(self.a, self.b, {}, self.pack, "pilot", "合成复核者", "a", "b")

    def test_disagreement_needs_bound_resolution(self):
        key = next(iter(self.b))
        self.b[key]["direction"] = "恶化"
        with self.assertRaises(ValueError):
            approve(self.a, self.b, {}, self.pack, "pilot", "合成复核者", "a", "b")
        resolution = completed(self.pack["main"][key], "R")
        resolution.update({"annotator_note": "虚构单元测试裁决", "independent_a_sha256": "wrong", "independent_b_sha256": "b"})
        with self.assertRaises(ValueError):
            approve(self.a, self.b, {key: resolution}, self.pack, "pilot", "合成复核者", "a", "b")
        resolution["independent_a_sha256"] = "a"
        rows = approve(self.a, self.b, {key: resolution}, self.pack, "pilot", "合成复核者", "a", "b")
        self.assertEqual(len(preflight_gold(rows, self.pack["main"], self.a)), 3)

    def test_gold_source_and_family_cannot_be_bypassed(self):
        rows = approve(self.a, self.b, {}, self.pack, "pilot", "合成复核者", "a", "b")
        bad = copy.deepcopy(rows)
        bad[0]["family_id"] = ""
        with self.assertRaises(ValueError):
            preflight_gold(bad, self.pack["main"])
        with self.assertRaises(ValueError):
            preflight_gold(rows, self.pack["main"], self.pack["main"])

    def test_sft_preflight_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "draft.csv", root / "must-not-exist"
            write_csv(source, list(self.a.values()))
            args = ["build_sft_dataset", "--input", str(source), "--pack", str(root), "--stage", "pilot", "--output-dir", str(output)]
            with patch("build_sft_dataset.load_pack", return_value=self.pack), patch("sys.argv", args):
                with self.assertRaises(ValueError):
                    build_sft_dataset.main()
            self.assertFalse(output.exists())

    def test_synthetic_accepted_sft_uses_only_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = approve(self.a, self.b, {}, self.pack, "pilot", "合成复核者", "a", "b")
            source, output = root / "synthetic-gold.csv", root / "sft"
            write_csv(source, rows)
            args = ["build_sft_dataset", "--input", str(source), "--pack", str(root), "--stage", "pilot", "--output-dir", str(output)]
            with patch("build_sft_dataset.load_pack", return_value=self.pack), patch("sys.argv", args):
                build_sft_dataset.main()
            data = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
            self.assertEqual(len(data), 3)
            self.assertEqual((output / "test.jsonl").read_text(), "")
            target = json.loads(data[0]["messages"][-1]["content"])
            self.assertEqual(set(target), {"direction", "causes", "evidence"})
            self.assertNotIn("合成复核者", data[0]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
