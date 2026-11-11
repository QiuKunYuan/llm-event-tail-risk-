#!/usr/bin/env python3
"""Stream selected RESSET XLSX fields into local compressed CSV files.

The source archives are read-only. Outputs are written to
RESSET论文数据/processed and must not be committed to Git.
"""

from __future__ import annotations

import csv
import argparse
import gzip
import html
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from resset_xlsx_audit import workbook_sheet_path


ROOT = Path("/home/admin/workspace/RESSET论文数据")
OUT = ROOT / "processed"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ROW_RE = re.compile(rb"<row\b[^>]*>(.*?)</row>", re.DOTALL)
ROW_OPEN_RE = re.compile(rb"<row\b[^>]*>")
ROW_CLOSE = b"</row>"
CELL_RE = re.compile(rb"<c\b([^>]*?)(?:>(.*?)</c>|/>)", re.DOTALL)
COL_RE = re.compile(rb'\br="([A-Z]+)\d+"')
TYPE_RE = re.compile(rb'\bt="([^"]+)"')
TEXT_RE = re.compile(rb"<t(?:\s[^>]*)?>(.*?)</t>", re.DOTALL)
VALUE_RE = re.compile(rb"<v>(.*?)</v>", re.DOTALL)


@dataclass(frozen=True)
class TableSpec:
    key: str
    archive_tokens: tuple[str, ...]
    output_fields: tuple[str, ...]
    source_aliases: dict[str, tuple[str, ...]]
    date_fields: tuple[str, ...] = ()
    require: tuple[str, ...] = ()


TABLES = (
    TableSpec(
        "daily_returns",
        ("日收益(",),
        ("company_code", "stock_code", "trading_stock_code", "date", "return", "capital_return", "risk_free_rate", "market_return_float", "market_return_total", "industry_sector", "industry_group"),
        {
            "company_code": ("上市公司代码_Comcd", "上市公司代码_ComCd", "公司代码()_CompanyCode", "公司代码_CompanyCode"),
            "stock_code": ("股票代码_Stkcd",),
            "trading_stock_code": ("交易时股票代码_Stkcdotrd",),
            "date": ("日期_Date",),
            "return": ("日收益率_Dret",),
            "capital_return": ("日资本收益率_Daret",),
            "risk_free_rate": ("日无风险收益率_DRfRet",),
            "market_return_float": ("流通市值加权平均市场日收益率_Drettmv",),
            "market_return_total": ("总市值加权平均市场日收益率_Dretmc",),
            "industry_sector": ("证监会行业门类代码_Csrciccd1",),
            "industry_group": ("证监会行业大类代码_Csrciccd2",),
        },
        ("date",),
        ("stock_code", "date", "return", "risk_free_rate"),
    ),
    TableSpec(
        "daily_factors",
        ("日三因子",),
        ("date", "exchange", "market", "rmrf_float", "smb_float", "hml_float", "rmrf_total", "smb_total", "hml_total"),
        {
            "date": ("日期_Date",),
            "exchange": ("交易所标识_Exchflg",),
            "market": ("市场标识_Mktflg",),
            "rmrf_float": ("市场溢酬因子__流通市值加权_Rmrf_tmv",),
            "smb_float": ("市值因子__流通市值加权_Smb_tmv",),
            "hml_float": ("账面市值比因子__流通市值加权_Hml_tmv",),
            "rmrf_total": ("市场溢酬因子__总市值加权_Rmrf_mc",),
            "smb_total": ("市值因子__总市值加权_Smb_mc",),
            "hml_total": ("账面市值比因子__总市值加权_Hml_mc",),
        },
        ("date",),
        ("date", "rmrf_float", "smb_float", "hml_float"),
    ),
    TableSpec(
        "industry_daily",
        ("证监会门类行业日收益",),
        ("date", "industry_sector", "industry_name", "return_float", "return_total", "capital_return_float"),
        {
            "date": ("日期_Date",),
            "industry_sector": ("证监会行业门类代码_Csrciccd1",),
            "industry_name": ("证监会行业门类名称_Csrcicnm1",),
            "return_float": ("行业日收益率_流通市值加权_Drettmv",),
            "return_total": ("行业日收益率_总市值加权_Dretmc",),
            "capital_return_float": ("行业日资本收益_流通市值加权()_Darettmv",),
        },
        ("date",),
        ("date", "industry_sector"),
    ),
    TableSpec(
        "earnings_events",
        ("业绩预告",),
        ("company_code", "stock_code", "publication_date", "report_date", "forecast_type", "result_summary", "forecast_text", "forecast_object", "growth_upper", "growth_lower", "profit_lower", "profit_upper", "last_profit"),
        {
            "company_code": ("上市公司代码_ComCd", "上市公司代码_Comcd"),
            "stock_code": ("A股股票代码_A_StkCd", "股票代码_Stkcd"),
            "publication_date": ("信息发布日期_InfoPubDt",),
            "report_date": ("截止日期_EndDt",),
            "forecast_type": ("业绩预测类型()_FrcstType",),
            "result_summary": ("业绩预测结果说明_ResStat",),
            "forecast_text": ("业绩预测内容描述_FrcstCon",),
            "forecast_object": ("预测对象()_FrcstObj",),
            "growth_upper": ("预测幅度上限(%)_EstGroRtFlr",),
            "growth_lower": ("预测幅度下限(%)_EstGroRtCei",),
            "profit_lower": ("预测净利润下限(元)_EstPrfFlr",),
            "profit_upper": ("预测净利润上限(元)_EstPrfCei",),
            "last_profit": ("上年同期净利润(元)_LastProf",),
        },
        ("publication_date", "report_date"),
        ("stock_code", "publication_date", "forecast_text"),
    ),
    TableSpec(
        "lawsuit_events",
        ("诉讼仲裁",),
        ("company_code", "stock_code", "first_publication_date", "publication_date", "announcement_type", "event_text", "behavior_text", "latest_status", "event_subject", "event_process"),
        {
            "company_code": ("上市公司代码_ComCd", "上市公司代码_Comcd"),
            "stock_code": ("A股股票代码_A_StkCd", "股票代码_Stkcd"),
            "first_publication_date": ("首次信息发布日期_IInfoPubDt",),
            "publication_date": ("信息发布日期_InfoPubDt",),
            "announcement_type": ("公告类型()_DclrType",),
            "event_text": ("事件内容_EventCon",),
            "behavior_text": ("行为描述_ActDes",),
            "latest_status": ("最新进展状态描述_NewestAdv",),
            "event_subject": ("事件主体()_EventSub",),
            "event_process": ("事件进程()_EventProc",),
        },
        ("first_publication_date", "publication_date"),
        ("stock_code", "publication_date"),
    ),
    TableSpec(
        "shareholders",
        ("主要股东名单",),
        ("company_code", "stock_code", "report_date", "publication_date", "holder_rank", "holder_id", "holder_name", "holder_attribute", "holder_nature", "holder_type", "total_shares", "float_shares", "holding_shares", "pct_total", "pct_float"),
        {
            "company_code": ("上市公司代码_ComCd", "上市公司代码_Comcd"),
            "stock_code": ("A股股票代码_A_StkCd", "股票代码_Stkcd"),
            "report_date": ("截止日期_EndDt",),
            "publication_date": ("信息发布日期_InfoPubDt",),
            "holder_rank": ("股东排名_ShRank",),
            "holder_id": ("股东ID_GDID",),
            "holder_name": ("股东名单_SHLst",),
            "holder_attribute": ("股东属性()_SHAttrib",),
            "holder_nature": ("股东性质_SHChrct",),
            "holder_type": ("股东类别_SHType",),
            "total_shares": ("公司总股数(股)_ComFullShr",),
            "float_shares": ("流通股总股数(股)_TrdShr",),
            "holding_shares": ("持股数(股)_HoldSum",),
            "pct_total": ("持股数占总股本比例_PctTotShr",),
            "pct_float": ("持股数占流通股比例_PctTrdShr",),
        },
        ("report_date", "publication_date"),
        ("stock_code", "publication_date", "holder_name"),
    ),
    TableSpec(
        "industry_history",
        ("证监会行业变更历史",),
        ("company_code", "stock_code", "publication_date", "effective_date", "cancel_date", "industry_code", "industry_sector"),
        {
            "company_code": ("上市公司代码_ComCd", "上市公司代码_Comcd"),
            "stock_code": ("股票代码_StkCd",),
            "publication_date": ("信息发布日期_InfoPubDt",),
            "effective_date": ("生效日期_EffDt",),
            "cancel_date": ("取消日期_CancelDt",),
            "industry_code": ("证监会行业代码_CsrcICCd",),
            "industry_sector": ("证监会行业门类代码_CsrcICCd1",),
        },
        ("publication_date", "effective_date", "cancel_date"),
        ("stock_code", "industry_sector"),
    ),
    TableSpec(
        "status_history",
        ("上市状态变更历史",),
        ("company_code", "stock_code", "change_date", "change_type", "change_reason", "change_description"),
        {
            "company_code": ("上市公司代码_ComCd", "上市公司代码_Comcd"),
            "stock_code": ("股票代码_StkCd",),
            "change_date": ("变更日期_ChgDt",),
            "change_type": ("变更类型_ChgType",),
            "change_reason": ("变更原因_ChgRea",),
            "change_description": ("变更说明_ChgStat",),
        },
        ("change_date",),
        ("stock_code", "change_date"),
    ),
)


def column_number(letters: bytes) -> int:
    value = 0
    for char in letters:
        value = value * 26 + char - 64
    return value - 1


def cell_value(attributes: bytes, body: bytes, shared_strings: list[str] | None) -> str:
    cell_type_match = TYPE_RE.search(attributes)
    cell_type = cell_type_match.group(1).decode("ascii") if cell_type_match else ""
    if cell_type == "inlineStr":
        return "".join(html.unescape(piece.decode("utf-8")) for piece in TEXT_RE.findall(body))
    value_match = VALUE_RE.search(body)
    raw = value_match.group(1).decode("utf-8") if value_match else ""
    if cell_type == "s" and shared_strings is not None and raw:
        return shared_strings[int(raw)]
    return html.unescape(raw)


def load_shared_strings(book: zipfile.ZipFile) -> list[str] | None:
    if "xl/sharedStrings.xml" not in book.namelist():
        return None
    with book.open("xl/sharedStrings.xml") as stream:
        first = stream.read(512)
    if b'uniqueCount="0"' in first or b'count="0"' in first:
        return None
    strings: list[str] = []
    with book.open("xl/sharedStrings.xml") as stream:
        for _, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == f"{{{MAIN_NS}}}si":
                strings.append("".join(node.text or "" for node in elem.findall(f".//{{{MAIN_NS}}}t")))
                elem.clear()
    return strings


def row_cells(row_bytes: bytes, wanted_columns: set[int], shared_strings: list[str] | None) -> dict[int, str]:
    values: dict[int, str] = {}
    for match in CELL_RE.finditer(row_bytes):
        attrs = match.group(1) or b""
        ref = COL_RE.search(attrs)
        if ref is None:
            continue
        column = column_number(ref.group(1))
        if column not in wanted_columns:
            continue
        body = match.group(2) or b""
        values[column] = cell_value(attrs, body, shared_strings)
    return values


def iter_row_bodies(stream, initial: bytes):
    """Yield worksheet row bodies while retaining only a small XML buffer."""
    buffer = initial
    cursor = 0
    while True:
        chunk = stream.read(8 * 1024 * 1024)
        eof = not chunk
        if chunk:
            buffer += chunk
        while True:
            opening = ROW_OPEN_RE.search(buffer, cursor)
            if opening is None:
                cursor = max(0, len(buffer) - 16)
                break
            close_at = buffer.find(ROW_CLOSE, opening.end())
            if close_at < 0:
                cursor = opening.start()
                break
            yield buffer[opening.end():close_at]
            cursor = close_at + len(ROW_CLOSE)
        if cursor:
            buffer = buffer[cursor:]
            cursor = 0
        if eof:
            break


def matching_archive(path: Path, spec: TableSpec) -> bool:
    name = path.name
    if spec.key == "daily_returns":
        return "日收益(" in name
    return all(token in name for token in spec.archive_tokens)


def convert_workbook(
    workbook_path: Path,
    archive_path: Path,
    member_name: str,
    spec: TableSpec,
    writer: csv.writer,
    stats: dict[str, object],
) -> None:
    with zipfile.ZipFile(workbook_path) as book:
        sheet_path = workbook_sheet_path(book)
        shared_strings = load_shared_strings(book)
        with book.open(sheet_path) as stream:
            initial = stream.read(256 * 1024)
            header_match = next(ROW_RE.finditer(initial), None)
            if header_match is None:
                raise ValueError("worksheet header row not found in first 256 KiB")
            header_cells = row_cells(header_match.group(1), set(range(512)), shared_strings)
            header_by_column = {column: value.strip() for column, value in header_cells.items()}
            aliases = {name: {alias.casefold() for alias in candidates} for name, candidates in spec.source_aliases.items()}
            source_column: dict[int, str] = {}
            resolved: dict[str, int] = {}
            for column, header in header_by_column.items():
                for output_name, options in aliases.items():
                    if header.casefold() in options and output_name not in resolved:
                        resolved[output_name] = column
                        source_column[column] = output_name
            missing_required = [name for name in spec.require if name not in resolved]
            if missing_required:
                raise ValueError(f"required columns not found: {missing_required}; headers={list(header_by_column.values())}")

            stats["input_files"] = int(stats.get("input_files", 0)) + 1
            stats.setdefault("sources", []).append({
                "archive": str(archive_path.relative_to(ROOT)),
                "workbook": member_name,
                "unmatched_optional_fields": [name for name in spec.output_fields if name not in resolved],
            })
            rows = 0
            missing_counts = stats.setdefault("missing_counts", {field: 0 for field in spec.output_fields})
            dates = stats.setdefault("date_ranges", {field: {"min": None, "max": None} for field in spec.date_fields})
            wanted_columns = set(source_column)

            # The header has already been consumed. RESSET sheets use one XML row per record.
            for body in iter_row_bodies(stream, initial[header_match.end():]):
                data = row_cells(body, wanted_columns, shared_strings)
                record = {field: data.get(resolved[field], "") if field in resolved else "" for field in spec.output_fields}
                for field in spec.output_fields:
                    value = record.get(field, "")
                    if not value:
                        missing_counts[field] = int(missing_counts[field]) + 1
                    if field in dates and value:
                        bound = dates[field]
                        if bound["min"] is None or value < bound["min"]:
                            bound["min"] = value
                        if bound["max"] is None or value > bound["max"]:
                            bound["max"] = value
                writer.writerow([record.get(field, "") for field in spec.output_fields])
                rows += 1
            stats["rows_written"] = int(stats.get("rows_written", 0)) + rows
            stats.setdefault("rows_by_workbook", []).append({"workbook": member_name, "rows": rows})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated table names; default converts all supported tables")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[2] / "RESSET论文数据", help="directory containing the exported RESSET ZIP files")
    parser.add_argument("--output", help="output directory; defaults to <root>/processed")
    args = parser.parse_args()
    global ROOT, OUT
    ROOT = Path(args.root).resolve()
    OUT = Path(args.output).resolve() if args.output else ROOT / "processed"
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"source_root": str(ROOT), "tables": {}, "skipped": ["monthly momentum is not used", "turnover export has no turnover value column"]}
    report_path = OUT / "preparation_report.json"
    if args.only and report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))

    archives = sorted(ROOT.rglob("*.zip"))
    selected_tables = {name.strip() for name in args.only.split(",")} if args.only else None
    for spec in TABLES:
        if selected_tables is not None and spec.key not in selected_tables:
            continue
        selected_archives = [path for path in archives if matching_archive(path, spec)]
        table_stats: dict[str, object] = {"rows_written": 0, "input_files": 0, "sources": [], "rows_by_workbook": [], "errors": []}
        output_path = OUT / f"{spec.key}.csv.gz"
        with gzip.open(output_path, "wt", encoding="utf-8", newline="", compresslevel=4) as output:
            writer = csv.writer(output)
            writer.writerow(spec.output_fields)
            for archive_path in selected_archives:
                try:
                    with zipfile.ZipFile(archive_path) as archive:
                        members = [item for item in archive.infolist() if item.filename.lower().endswith(".xlsx")]
                        for member in members:
                            try:
                                with tempfile.NamedTemporaryFile(suffix=".xlsx") as temp:
                                    with archive.open(member) as source:
                                        shutil.copyfileobj(source, temp)
                                    temp.flush()
                                    convert_workbook(Path(temp.name), archive_path, member.filename, spec, writer, table_stats)
                            except Exception as exc:
                                table_stats["errors"].append({"workbook": member.filename, "error": str(exc)})
                except Exception as exc:
                    table_stats["errors"].append({"archive": str(archive_path), "error": str(exc)})
        table_stats["output"] = str(output_path)
        report["tables"][spec.key] = table_stats

    report["source_root"] = str(ROOT)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
