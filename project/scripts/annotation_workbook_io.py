"""Small, macro-free XLSX annotation workbooks using only the standard library."""
from html import escape
import re
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def letters(number):
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def column(ref):
    value = 0
    for char in ref:
        if not char.isalpha():
            break
        value = value * 26 + ord(char.upper()) - 64
    return value - 1


def xml_text(value):
    text = str(value)
    if len(text) > 32767:
        raise ValueError("Text exceeds Excel's cell limit; never silently truncate a source")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in text):
        raise ValueError("Text contains an XML control character")
    return escape(text).replace("\r", "&#13;")


def write_workbook(path, sheets, editable=None, dropdowns=None):
    """Rows are strings, even stock codes and text beginning with '='."""
    if path.exists():
        raise FileExistsError(path)
    editable = editable or {}
    dropdowns = dropdowns or {}
    documents = {}
    for i, (name, rows) in enumerate(sheets.items(), 1):
        if not name or len(name) > 31 or any(c in name for c in "[]:*?/\\"):
            raise ValueError("Invalid worksheet name")
        width = max((len(row) for row in rows), default=1)
        cells = []
        for r, values in enumerate(rows, 1):
            entries = []
            for c, value in enumerate(values, 1):
                style = 2 if r == 1 else 1 if c - 1 in editable.get(name, set()) else 0
                entries.append(f'<c r="{letters(c)}{r}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>')
            height = ' ht="96" customHeight="1"' if name == "待标注" and r > 1 else ""
            cells.append(f'<row r="{r}"{height}>' + "".join(entries) + '</row>')
        validations = []
        for c, options in dropdowns.get(name, {}).items():
            formula = '"' + ','.join(options) + '"'
            validations.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorTitle="请从列表选择" error="不要改写选项" sqref="{letters(c + 1)}2:{letters(c + 1)}{max(2, len(rows))}"><formula1>{escape(formula)}</formula1></dataValidation>')
        cols = ''.join(f'<col min="{c}" max="{c}" width="{78 if name == "待标注" and c == 2 else 34 if c in (5, 6, 7, 8, 11) else 23}" customWidth="1"/>' for c in range(1, width + 1))
        protection = '<sheetProtection sheet="1" objects="1" scenarios="1" autoFilter="0" formatRows="0" formatColumns="0"/>' if name in editable else ""
        filters = f'<autoFilter ref="A1:{letters(width)}{len(rows)}"/>' if name == "待标注" else ""
        documents[f'xl/worksheets/sheet{i}.xml'] = (
            f'<worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            + '<cols>' + cols + '</cols><sheetData>' + ''.join(cells) + '</sheetData>' + protection + filters
            + (f'<dataValidations count="{len(validations)}">' + ''.join(validations) + '</dataValidations>' if validations else "") + '</worksheet>'
        )
    names = list(sheets)
    documents['xl/workbook.xml'] = f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>' + ''.join(f'<sheet name="{escape(n, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1)) + '</sheets></workbook>'
    documents['xl/_rels/workbook.xml.rels'] = f'<Relationships xmlns="{PKG}">' + ''.join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1)) + f'<Relationship Id="rIdStyles" Type="{REL}/styles" Target="styles.xml"/></Relationships>'
    documents['_rels/.rels'] = f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    documents['[Content_Types].xml'] = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names) + 1)) + '</Types>'
    documents['xl/styles.xml'] = f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyProtection="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/><protection locked="0"/></xf><xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    with ZipFile(path, "x", ZIP_DEFLATED) as book:
        for name, content in documents.items():
            book.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content)


def read_workbook(path):
    """Accept Excel/WPS shared or inline strings; refuse formulas/external links."""
    with ZipFile(path) as book:
        entries = book.infolist()
        if len(entries) > 256 or sum(p.file_size for p in entries) > 128 * 1024 * 1024:
            raise ValueError("Workbook exceeds bounded annotation-file limits")
        if any('vbaproject' in p.filename.lower() or 'externallink' in p.filename.lower() for p in entries):
            raise ValueError("Macros and external links are not annotation inputs")
        for entry in entries:
            if entry.filename.endswith('.rels'):
                relationships = ET.fromstring(book.read(entry.filename))
                if any(p.get('TargetMode') == 'External' for p in relationships):
                    raise ValueError("External relationships are not annotation inputs")
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            root = ET.fromstring(book.read('xl/sharedStrings.xml'))
            shared = [''.join(p.text or '' for p in node.iter(f'{{{NS}}}t')) for node in root]
        relations = {p.get('Id'): p.get('Target') for p in ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))}
        result = {}
        for sheet in ET.fromstring(book.read('xl/workbook.xml')).find(f'{{{NS}}}sheets'):
            target = relations[sheet.get(f'{{{REL}}}id')]
            file = str(PurePosixPath(target.lstrip('/'))) if target.startswith('/') else str(PurePosixPath('xl') / target)
            if '..' in PurePosixPath(file).parts or not file.startswith('xl/'):
                raise ValueError("Invalid worksheet relationship")
            root = ET.fromstring(book.read(file))
            rows = []
            for row in root.findall(f'{{{NS}}}sheetData/{{{NS}}}row'):
                values = {}
                for cell in row:
                    if cell.find(f'{{{NS}}}f') is not None:
                        raise ValueError("Formulas are forbidden in annotation submissions")
                    kind = cell.get('t')
                    value = cell.find(f'{{{NS}}}v')
                    if kind == 'inlineStr':
                        text = ''.join(t.text or '' for t in cell.iter(f'{{{NS}}}t'))
                    elif kind == 's':
                        text = shared[int(value.text)] if value is not None else ''
                    elif kind in ('e', 'b'):
                        raise ValueError("Error/boolean cells cannot replace annotation text")
                    else:
                        text = value.text or '' if value is not None else ''
                    ref = cell.get('r', '')
                    if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', ref):
                        raise ValueError("Invalid Excel cell reference")
                    index = column(ref)
                    if index >= 16384 or int(re.search(r'[0-9]+', ref).group()) > 1048576 or index in values:
                        raise ValueError("Out-of-range or duplicate Excel cell")
                    values[index] = text
                if values:
                    rows.append([values.get(i, '') for i in range(max(values) + 1)])
            result[sheet.get('name')] = rows
        return result
