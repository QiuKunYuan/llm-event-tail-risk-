#!/usr/bin/env python3
"""Read-only audit of XLSX workbooks inside the exported RESSET ZIP files."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def workbook_sheet_path(book: zipfile.ZipFile) -> str:
    root = ET.fromstring(book.read("xl/workbook.xml"))
    sheet = root.find(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet")
    if sheet is None:
        raise ValueError("workbook has no sheets")
    rel_id = sheet.attrib[f"{{{REL_NS}}}id"]
    rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    target = next(
        rel.attrib["Target"]
        for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship")
        if rel.attrib["Id"] == rel_id
    )
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("xl") / target)


def first_row(book: zipfile.ZipFile, sheet_path: str) -> list[tuple[str, int | None]]:
    with book.open(sheet_path) as stream:
        for _, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == f"{{{MAIN_NS}}}row":
                cells = []
                for cell in elem.findall(f"{{{MAIN_NS}}}c"):
                    value = cell.find(f"{{{MAIN_NS}}}v")
                    raw = value.text if value is not None else ""
                    if cell.attrib.get("t") == "s":
                        cells.append(("", int(raw)))
                    elif cell.attrib.get("t") == "inlineStr":
                        text = "".join(
                            node.text or ""
                            for node in cell.findall(f".//{{{MAIN_NS}}}t")
                        )
                        cells.append((text, None))
                    else:
                        cells.append((raw, None))
                return cells
                
    return []


def resolve_shared_strings(book: zipfile.ZipFile, indices: set[int]) -> dict[int, str]:
    resolved: dict[int, str] = {}
    if not indices:
        return resolved
    wanted = max(indices)
    if "xl/sharedStrings.xml" not in book.namelist():
        return resolved
    shared_string_index = 0
    with book.open("xl/sharedStrings.xml") as stream:
        for _, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag != f"{{{MAIN_NS}}}si":
                continue
            if shared_string_index in indices:
                resolved[shared_string_index] = "".join(
                    node.text or "" for node in elem.findall(f".//{{{MAIN_NS}}}t")
                )
            elem.clear()
            if shared_string_index >= wanted:
                break
            shared_string_index += 1
    return resolved


def declared_dimension(book: zipfile.ZipFile, sheet_path: str) -> str | None:
    with book.open(sheet_path) as stream:
        for _, elem in ET.iterparse(stream, events=("start",)):
            if elem.tag == f"{{{MAIN_NS}}}dimension":
                return elem.attrib.get("ref")
    return None


def count_rows_fast(book: zipfile.ZipFile, sheet_path: str) -> int:
    total = 0
    carry = b""
    with book.open(sheet_path) as stream:
        while block := stream.read(8 * 1024 * 1024):
            block = carry + block
            total += block.count(b"<row ")
            carry = block[-8:]
    return total


def audit_xlsx(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as book:
        sheet_path = workbook_sheet_path(book)
        cells = first_row(book, sheet_path)
        string_indices = {index for _, index in cells if index is not None}
        strings = resolve_shared_strings(book, string_indices)
        headers = [strings.get(index, "") if index is not None else value for value, index in cells]
        return {
            "workbook": path.name,
            "sheet": sheet_path,
            "worksheet_dimension": declared_dimension(book, sheet_path),
            "rows_including_header": count_rows_fast(book, sheet_path),
            "headers": headers,
            "size_bytes": path.stat().st_size,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=Path(__file__).resolve().parents[2] / "RESSET论文数据")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for archive_path in sorted(root.rglob("*.zip")):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = [
                    item for item in archive.infolist()
                    if item.filename.lower().endswith(".xlsx")
                ]
                for member in members:
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".xlsx") as temp:
                            with archive.open(member) as source:
                                shutil.copyfileobj(source, temp)
                            temp.flush()
                            report = audit_xlsx(Path(temp.name))
                        report["workbook"] = member.filename
                        report["source_archive"] = str(archive_path.relative_to(root))
                        result.append(report)
                    except Exception as exc:  # preserve other workbooks' audit results
                        errors.append({"workbook": member.filename, "error": str(exc)})
        except Exception as exc:
            errors.append({"archive": str(archive_path), "error": str(exc)})

    output = {"root": str(root), "workbooks": result, "errors": errors}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
