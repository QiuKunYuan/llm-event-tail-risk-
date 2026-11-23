"""Export authoritative Markdown drafts to editable Word using stdlib OOXML.

Equations remain source notation; final school formatting needs equation editing.
"""
from pathlib import Path
from html import escape
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def paragraph(text, style="Normal"):
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = text.replace("**", "").replace("`", "")
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def export(source):
    paragraphs = []
    code = False
    table_rows = []
    def flush_table():
        if not table_rows:
            return
        rows = []
        for values in table_rows:
            cells = ''.join('<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>' + paragraph(value) + '</w:tc>' for value in values)
            rows.append('<w:tr>' + cells + '</w:tr>')
        border = ''.join(f'<w:{k} w:val="single" w:sz="4" w:color="B6C4D0"/>' for k in ['top','left','bottom','right','insideH','insideV'])
        paragraphs.append('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>' + border + '</w:tblBorders></w:tblPr>' + ''.join(rows) + '</w:tbl>')
        table_rows.clear()
    for line in source.read_text().splitlines():
        if line.startswith('|') and not code:
            values = [x.strip() for x in line.strip('|').split('|')]
            if not all(re.fullmatch(r'[:\-\s]+', x) for x in values):
                table_rows.append(values)
            continue
        flush_table()
        if line.startswith("```"):
            code = not code;continue
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        if heading:
            paragraphs.append(paragraph(heading[2], "Heading" + str(min(3, len(heading[1])))))
        else:
            paragraphs.append(paragraph(line, "Code" if code else "Normal"))
    flush_table()
    target = source.with_suffix(".docx")
    document = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + ''.join(paragraphs) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:bottom="1440" w:left="1440" w:right="1440"/></w:sectPr></w:body></w:document>'
    styles = '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="宋体"/><w:sz w:val="24"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    for i in range(1,4):
        styles += f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:pPr><w:keepNext/><w:spacing w:before="240"/><w:outlineLvl w:val="{i-1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{40-i*4}"/></w:rPr></w:style>'
    styles += '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Code"><w:rPr><w:rFonts w:ascii="Consolas"/><w:sz w:val="20"/></w:rPr></w:style></w:styles>'
    with ZipFile(target, "w", ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', document)
        z.writestr('word/styles.xml',styles)
        z.writestr('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    print(target)

if __name__ == "__main__":
    for source in [ROOT / "docs/开题方案/实施版研究设计.md", ROOT / "docs/开题报告/开题报告正文-确定方案.md"]:
        export(source)
