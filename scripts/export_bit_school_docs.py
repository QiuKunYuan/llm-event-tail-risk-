"""Fill converted BIT master's templates; never fabricate assessment or signatures.

Requires python-docx. Convert the user's original .doc forms to .docx separately;
pass that private directory with --templates. Original school files are read-only.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import Table, _Cell

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/开题报告/北理工专硕"
TITLE = "披露时点感知的A股短期尾部风险预测"
HEADINGS = ["一 选题依据", "二 研究内容", "三 研究方案", "四 研究工作进度安排",
            "五 预期研究成果", "六 本课题创新之处", "七 研究基础"]
COVER = {"学号": "待填写", "姓名": "邱琨原", "导师": "待填写",
         "类别": "待填写", "领域": "待填写", "培养单位": "待填写",
         "学科": "待填写", "研究方向": "金融风险预测与图学习"}


def normalized(value):
    return re.sub(r"\s+", "", value)


def style_run(run, size=12, bold=False):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")


def format_paragraph(paragraph, line=22, heading=False):
    fmt = paragraph.paragraph_format
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(line)
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(6)
    fmt.keep_with_next = heading
    fmt.widow_control = True
    fmt.first_line_indent = Pt(0 if heading else 24)
    for run in paragraph.runs:
        style_run(run, bold=heading)


def clear_cell(cell):
    for child in list(cell._tc):
        if child.tag != qn("w:tcPr"):
            cell._tc.remove(child)
    cell.add_paragraph()


def set_cell(cell, text, size=16):
    clear_cell(cell)
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    paragraph.paragraph_format.line_spacing = Pt(30)
    style_run(paragraph.add_run(text), size)


def fill_cover(document, title):
    values = dict(COVER, 选题名称=title, 报告题目=title)
    filled = set()
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if not cells or cells[-1]._tc is not cell._tc:
                    cells.append(cell)
            for i, cell in enumerate(cells[:-1]):
                label = normalized(cell.text)
                if label in values and label not in filled:
                    set_cell(cells[i + 1], values[label])
                    filled.add(label)
    required = {"学号", "姓名", "导师", "培养单位"}
    if not required <= filled or not ("选题名称" in filled or "报告题目" in filled):
        raise ValueError(f"Unmatched cover fields: {sorted(required - filled)}")
    return sorted(filled)


def grid_table(table):
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        for name, value in (("val", "single"), ("sz", "4"), ("color", "000000")):
            border.set(qn("w:" + name), value)
        borders.append(border)
    table._tbl.tblPr.append(borders)


def append_markdown(container, text, line):
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        value = lines[index].strip()
        index += 1
        if not value:
            continue
        if value.startswith("|"):
            rows = [value]
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            parsed = []
            for row in rows:
                cells = [part.strip() for part in row.strip("|").split("|")]
                if all(re.fullmatch(r"[:\-\s]+", cell) for cell in cells):
                    continue
                parsed.append(cells)
            width = max(len(row) for row in parsed)
            if isinstance(container, _Cell):
                table = container.add_table(rows=len(parsed), cols=width)
            else:
                table = container.add_table(rows=len(parsed), cols=width)
            grid_table(table)
            for r, row in enumerate(parsed):
                for c, item in enumerate(row):
                    paragraph = table.cell(r, c).paragraphs[0]
                    paragraph.add_run(item)
                    format_paragraph(paragraph, line, heading=r == 0)
                    paragraph.paragraph_format.first_line_indent = Pt(0)
            continue
        heading = bool(re.match(r"^#{2,4}\s", value))
        value = re.sub(r"^#{1,4}\s+", "", value).replace("**", "").replace("`", "")
        if isinstance(container, _Cell) and len(container.paragraphs) == 1 and not container.paragraphs[0].text:
            paragraph = container.paragraphs[0]
            paragraph.add_run(value)
        else:
            paragraph = container.add_paragraph(value)
        format_paragraph(paragraph, line, heading)


def allow_table_pages(document, body_tables=None):
    for table in body_tables if body_tables is not None else document.tables:
        text = "".join(node.text or "" for node in table._tbl.iter(qn("w:t")))
        if body_tables is None and not any(normalized(heading) in normalized(text) for heading in HEADINGS):
            continue
        for row in table.rows:
            props = row._tr.get_or_add_trPr()
            for child in list(props):
                if child.tag in (qn("w:cantSplit"), qn("w:trHeight")):
                    props.remove(child)


def page_number(paragraph):
    if any((node.text or "").strip() == "PAGE" for node in paragraph._p.iter(qn("w:instrText"))):
        return
    for kind in ("begin", "instruction", "separate", "value", "end"):
        run = paragraph.add_run("1" if kind == "value" else "")
        style_run(run, 10.5)
        if kind == "instruction":
            node = OxmlElement("w:instrText")
            node.text = " PAGE "
            run._r.append(node)
        elif kind != "value":
            node = OxmlElement("w:fldChar")
            node.set(qn("w:fldCharType"), kind)
            run._r.append(node)


def set_running_style(document, review=False):
    for index, section in enumerate(document.sections):
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        if review:
            section.left_margin, section.right_margin = Cm(3), Cm(2.4)
            section.header_distance, section.footer_distance = Cm(2.5), Cm(1.5)
            if any(paragraph.text.strip() for paragraph in section.header.paragraphs):
                section.top_margin = max(section.top_margin, Cm(3.6))
        for paragraph in section.header.paragraphs:
            if paragraph.text.strip():
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    style_run(run, 15, True)
        if not review and index == 0:
            section.different_first_page_header_footer = True
            source_header = [deepcopy(child) for child in section.header._element]
            first_header = section.first_page_header._element
            for child in list(first_header):
                first_header.remove(child)
            for child in source_header:
                first_header.append(child)
        if not review and index > 0:
            section.different_first_page_header_footer = False
        if (review and index > 0) or not review:
            if review:
                section.different_first_page_header_footer = False
                if index == 1:
                    numbering = section._sectPr.find(qn("w:pgNumType"))
                    if numbering is None:
                        numbering = OxmlElement("w:pgNumType")
                        section._sectPr.append(numbering)
                    numbering.set(qn("w:start"), "1")
            page_number(section.footer.paragraphs[0])
        for paragraph in section.footer.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            for run in paragraph.runs:
                style_run(run, 10.5)


def save_draft(document, output):
    document.core_properties.title = output.stem
    document.core_properties.author = ""
    document.core_properties.last_modified_by = ""
    document.core_properties.comments = "学校模板适配草稿；导师评阅、正式评审和签署尚待实际完成。"
    document.save(output)


def proposal(template, output):
    document = Document(template)
    fields = fill_cover(document, TITLE)
    text = (SOURCE / "开题报告-邱琨原.md").read_text(encoding="utf-8")
    chunks = re.split(r"^## (.+)$", text, flags=re.M)
    sections = {chunks[i].strip(): chunks[i + 1] for i in range(1, len(chunks), 2)}
    if list(sections) != HEADINGS:
        raise ValueError("Proposal must contain exactly the seven school headings")
    found, body_tables = [], []
    for heading in HEADINGS:
        outside = [paragraph for paragraph in document.paragraphs
                   if normalized(paragraph.text) == normalized(heading)]
        if len(outside) == 1:
            paragraph = outside[0]
            block = paragraph._p.getnext()
            while block is not None and block.tag == qn("w:p") and not list(block.iter(qn("w:t"))):
                block = block.getnext()
            if block is None or block.tag != qn("w:tbl"):
                raise ValueError(f"No content table after school heading: {heading}")
            table = Table(block, document)
            cells = list(block.iter(qn("w:tc")))
            if len(cells) != 1:
                raise ValueError(f"Expected one school content cell: {heading}")
            cell = _Cell(cells[0], table)
            clear_cell(cell)
            format_paragraph(paragraph, 22, True)
        else:
            candidates = []
            for table in document.tables:
                for element in table._tbl.iter(qn("w:tc")):
                    words = "".join(node.text or "" for node in element.iter(qn("w:t")))
                    if normalized(words).startswith(normalized(heading)):
                        candidates.append((table, _Cell(element, table)))
            if len(candidates) != 1:
                raise ValueError(f"Ambiguous school section: {heading}: {len(candidates)}")
            table, cell = candidates[0]
            clear_cell(cell)
            cell.paragraphs[0].add_run(heading)
            format_paragraph(cell.paragraphs[0], 22, True)
        append_markdown(cell, sections[heading], 22)
        body_tables.append(table)
        found.append(heading)
    allow_table_pages(document, body_tables)
    set_running_style(document)
    save_draft(document, output)
    return {"headings": found, "cover_fields": fields, "body_line_points": 22}


def review(template, output):
    document = Document(template)
    fields = fill_cover(document, TITLE + "研究综述")
    if len(document.sections) < 2:
        raise ValueError("Review template must distinguish its cover and body sections")
    cover_section = deepcopy(document.sections[0]._sectPr)
    body = document._element.body
    first_table, removed = False, 0
    for child in list(body):
        if child.tag == qn("w:tbl"):
            if first_table:
                break
            first_table = True
        elif first_table and child.tag == qn("w:p") and removed < 2:
            if not list(child.iter(qn("w:t"))) and not list(child.iter(qn("w:drawing"))):
                body.remove(child)
                removed += 1
    instructions = None
    for index, child in enumerate(body):
        text = "".join(node.text or "" for node in child.iter(qn("w:t")))
        if "文献综述报告要求与打印格式说明" in text:
            instructions = index
            break
    if instructions is None:
        raise ValueError("Review instruction-page marker absent")
    for child in list(body)[instructions:]:
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    # Preserve the real cover, but do not leave the unused instruction/body pages.
    while len(body) > 1:
        child = body[-2] if body[-1].tag == qn("w:sectPr") else body[-1]
        text = "".join(node.text or "" for node in child.iter(qn("w:t")))
        if child.tag != qn("w:p") or text.strip():
            break
        body.remove(child)
    section_type = cover_section.find(qn("w:type"))
    if section_type is None:
        section_type = OxmlElement("w:type")
        cover_section.append(section_type)
    section_type.set(qn("w:val"), "nextPage")
    document.add_paragraph()._p.get_or_add_pPr().append(cover_section)
    text = (SOURCE / "文献综述-邱琨原.md").read_text(encoding="utf-8")
    text = "## 一、" + text.split("## 一、", 1)[1]
    append_markdown(document, text, 26)
    document.add_page_break()
    paragraph = document.add_paragraph("指导教师评阅意见（由导师填写）")
    format_paragraph(paragraph, 26, True)
    for _ in range(10):
        format_paragraph(document.add_paragraph(), 26)
    format_paragraph(document.add_paragraph("指导教师签字：________________"), 26)
    format_paragraph(document.add_paragraph("日期：_______年____月____日"), 26)
    set_running_style(document, True)
    save_draft(document, output)
    return {"cover_fields": fields, "body_line_points": 26,
            "teacher_opinion_and_signature": "blank_for_human_completion"}


def assessment(template, output):
    document = Document(template)
    fields = {"学号": "待填写", "姓名": "邱琨原", "导师": "待填写",
              "研究生培养单位": "待填写", "类别领域": "待填写", "选题名称": TITLE,
              "课题经费来源": "待确认", "开题报告时间": "待填写"}
    filled = set()
    for table in document.tables:
        for row in table.rows:
            unique = []
            for cell in row.cells:
                if not unique or unique[-1]._tc is not cell._tc:
                    unique.append(cell)
            for i, cell in enumerate(unique[:-1]):
                label = normalized(cell.text)
                if label in fields and label not in filled:
                    set_cell(unique[i + 1], fields[label], 12)
                    filled.add(label)
    if not {"姓名", "选题名称"} <= filled:
        raise ValueError("Assessment identification fields absent")
    # No selection, grade, credits, committee names, opinion or signature is filled.
    save_draft(document, output)
    return {"prefilled_fields": sorted(filled), "assessment_and_signatures": "not_filled"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=SOURCE)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [("附件8", "开题报告-邱琨原.docx", proposal),
            ("附件6", "文献综述-邱琨原.docx", review),
            ("附件11", "开题评审表-邱琨原-待导师评审.docx", assessment)]
    results = {}
    for prefix, filename, build in jobs:
        templates = list(args.templates.glob(prefix + "*.docx"))
        if len(templates) != 1:
            raise ValueError(f"Expected one converted template: {prefix}")
        target = args.output_dir / filename
        if target.exists() and not args.overwrite:
            raise FileExistsError(target)
        results[filename] = build(templates[0], target)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
