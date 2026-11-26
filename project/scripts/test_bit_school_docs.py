"""Synthetic form regressions; no institutional approval is inferred from output."""
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt
except ImportError:
    Document = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


@unittest.skipIf(Document is None, "Optional document dependency is not installed")
class SchoolDocumentTests(unittest.TestCase):
    def setUp(self):
        import export_bit_school_docs
        self.module = export_bit_school_docs
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cover(self):
        document = Document()
        table = document.add_table(rows=5, cols=2)
        for row, label in zip(table.rows, ("选题名称", "学号", "姓名", "导师", "培养单位")):
            row.cells[0].text = label
            row.cells[1].text = ""
        return document

    def test_cover_does_not_guess_unprovided_information(self):
        document = self.cover()
        fields = self.module.fill_cover(document, self.module.TITLE)
        self.assertIn("姓名", fields)
        values = {row.cells[0].text: row.cells[1].text for row in document.tables[0].rows}
        self.assertEqual(values["姓名"], "邱琨原")
        self.assertEqual(values["选题名称"], self.module.TITLE)
        for label in ("学号", "导师", "培养单位"):
            self.assertEqual(values[label], "待填写")

    def test_required_cover_fields_cannot_silently_disappear(self):
        document = Document()
        with self.assertRaises(ValueError):
            self.module.fill_cover(document, self.module.TITLE)

    def test_body_font_size_and_fixed_line_spacing(self):
        document = Document()
        for line in (22, 26):
            paragraph = document.add_paragraph("虚构测试段落")
            self.module.format_paragraph(paragraph, line)
            self.assertEqual(paragraph.paragraph_format.line_spacing, Pt(line))
            self.assertEqual(paragraph.runs[0].font.size, Pt(12))
            fonts = paragraph.runs[0]._element.get_or_add_rPr().get_or_add_rFonts()
            self.assertEqual(fonts.get(qn("w:eastAsia")), "宋体")

    def test_retired_short_proposal_not_recreated(self):
        self.assertFalse(hasattr(self.module, "proposal"))
        self.assertEqual(self.module.TITLE, "基于多维时序数据融合的短期证券市场风险预测")
        source = Path(self.module.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"附件8",', source)

    def test_assessment_does_not_fill_committee_result_or_signature(self):
        document = Document()
        table = document.add_table(rows=4, cols=2)
        for row, (label, value) in zip(table.rows, [
            ("姓名", ""), ("选题名称", ""),
            ("评审组意见", ""), ("考核结果", "□通过  □再审核  □不通过")
        ]):
            row.cells[0].text, row.cells[1].text = label, value
        document.add_paragraph("评审组签字：________________")
        template, output = self.path / "assessment-template.docx", self.path / "assessment.docx"
        document.save(template)
        self.module.assessment(template, output)
        result = Document(output)
        self.assertEqual(result.tables[0].cell(0, 1).text, "邱琨原")
        self.assertEqual(result.tables[0].cell(2, 1).text, "")
        self.assertEqual(result.tables[0].cell(3, 1).text, "□通过  □再审核  □不通过")
        self.assertIn("评审组签字：________________", [p.text for p in result.paragraphs])

    def test_review_has_thirty_references_and_conservative_body_count(self):
        text = (self.module.SOURCE / "文献综述-邱琨原.md").read_text(encoding="utf-8")
        body, references = text.split("## 参考文献", 1)
        body = body.split("## 一、", 1)[1]
        self.assertGreaterEqual(len(re.findall(r"[一-鿿]", body)), 3000)
        numbers = [int(value) for value in re.findall(r"^\[(\d+)\] ", references, re.M)]
        self.assertEqual(numbers, list(range(1, 35)))
        self.assertIn("不证明", (self.module.SOURCE / "提交说明与待填信息.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
