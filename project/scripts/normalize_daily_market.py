#!/usr/bin/env python3
"""Normalize selected RESSET DRESSTK CSV exports without loading them in memory."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import sys
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from prepare_resset import TableSpec, resolve_columns as resolve_source_columns


ALIASES: dict[str, tuple[str, ...]] = {
    "company_code": ("上市公司代码_Comcd", "上市公司代码_ComCd"),
    "raw_company_code": ("公司代码_CompanyCode", "公司代码()_CompanyCode"),
    "raw_security_code": ("证券代码_R_SecuCode",),
    "stock_code": (
        "股票代码_Stkcd",
        "股票代码_StkCd",
        "A股股票代码_A_StkCd",
    ),
    "trading_stock_code": ("交易时股票代码_Stkcdotrd", "交易时股票代码_StkCdOTrd"),
    "date": ("日期_Date", "交易日期", "Date", "date"),
    "listed_state": ("上市状态_Listedstate", "上市状态_ Listedstate"),
    "industry_sector": ("证监会行业门类代码_Csrciccd1",),
    "close": ("收盘价 (元)_Clpr", "收盘价（元）_Clpr", "收盘价_Clpr", "Clpr"),
    "adj_close_1": ("复权价1 (元)_AdjClpr1", "复权价1（元）_AdjClpr1", "AdjClpr1"),
    "adj_close_2": ("复权价2 (元)_AdjClpr2", "复权价2（元）_AdjClpr2", "AdjClpr2"),
    "adjustment_factor": ("累积股价调整乘子_Mcfacpr", "Mcfacpr"),
    "volume": ("成交量 (股)_Trdvol", "成交量（股）_Trdvol", "Trdvol"),
    "amount": ("成交金额 (元)_Trdsum", "成交金额（元）_Trdsum", "Trdsum"),
    "turnover_total": ("总股数日换手率_DFulTurnR", "DFulTurnR"),
    "turnover_float": ("流通股日换手率_DTrdTurnR", "DTrdTurnR"),
    "total_shares": ("总股数 (股)_Fullshr", "总股数（股）_Fullshr", "Fullshr"),
    "float_shares": ("流通股 (股)_Trdshr", "流通股（股）_Trdshr", "Trdshr"),
    "listed_float_shares": (
        "已上市流通股 (股)_Lsttrdshr",
        "已上市流通股（股）_Lsttrdshr",
        "Lsttrdshr",
    ),
    "share_change_date": ("股数变动日_Capchgdt", "Capchgdt"),
    "return": ("日收益率_Dret", "Dret"),
    "capital_return": ("日资本收益率_Daret", "Daret"),
    "ex_date": ("除权除息日_Exdt", "Exdt"),
    "cash_dividend": ("现金红利 (元)_Dividend", "现金红利（元）_Dividend", "Dividend"),
}

OUTPUT_FIELDS = (
    "company_code",
    "stock_code",
    "trading_stock_code",
    "date",
    "listed_state",
    "industry_sector",
    "close",
    "adj_close_1",
    "adj_close_2",
    "adjustment_factor",
    "volume",
    "amount",
    "turnover_total",
    "turnover_float",
    "total_shares",
    "float_shares",
    "listed_float_shares",
    "share_change_date",
    "return",
    "capital_return",
    "ex_date",
    "cash_dividend",
    "market_cap_total_derived",
    "market_cap_float_derived",
    "raw_company_code",
    "raw_security_code",
    "company_code_field",
    "source_file",
    "source_row",
)


def compact(value: str) -> str:
    return "".join(value.strip().split())


def resolve_columns(headers: list[str]) -> dict[str, str]:
    spec = TableSpec("daily_market", (), tuple(ALIASES),
                     {field: tuple(compact(alias) for alias in aliases)
                      for field, aliases in ALIASES.items()})
    resolved = resolve_source_columns({i: compact(header) for i, header in enumerate(headers)}, spec)
    return {field: headers[index] for field, index in resolved.items()}


@contextmanager
def open_csv_member(path: Path) -> Iterator[tuple[str, TextIO]]:
    """Open plain CSV, gzip CSV, or a ZIP containing exactly one CSV."""
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [
                name for name in archive.namelist()
                if name.lower().endswith(".csv") and not name.endswith("/")
            ]
            if len(members) != 1:
                raise ValueError(
                    f"{path}: expected exactly one CSV in ZIP, found {len(members)}"
                )
            with archive.open(members[0]) as binary:
                text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                yield f"{path}:{members[0]}", text
                text.detach()
    elif suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as text:
            yield str(path), text
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as text:
            yield str(path), text


def numeric(value: str) -> float | None:
    value = value.strip().replace(",", "")
    if value in ("", "null", "NULL", "NA", "N/A", "--", "—", "-"):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def canonical_date(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    raise ValueError(f"Unsupported date value: {value!r}")


def as_output_value(field: str, value: str) -> str:
    if field in ("date", "share_change_date", "ex_date"):
        return canonical_date(value) if value.strip() else ""
    return value.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--require-p0",
        action="store_true",
        help="fail unless price, volume, turnover and share-count fields are present",
    )
    args = parser.parse_args()
    for path in args.input:
        if not path.is_file():
            raise FileNotFoundError(path)

    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("Licensed market records must be written outside the repository")
    temp_output = args.output.with_name(args.output.name + ".tmp")
    key_path = args.output.with_name(args.output.name + ".keys.sqlite")
    if args.output.exists() or temp_output.exists() or key_path.exists():
        raise FileExistsError("Use a new private output path; existing records are not overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    keys = sqlite3.connect(key_path)
    keys.execute("PRAGMA cache_size=-16384")
    keys.execute("CREATE TABLE keys(stock TEXT, day TEXT, PRIMARY KEY(stock, day)) WITHOUT ROWID")
    rows_written = 0
    missing_by_file: dict[str, list[str]] = {}
    min_date: str | None = None
    max_date: str | None = None
    try:
        with gzip.open(temp_output, "wt", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(
                destination, fieldnames=OUTPUT_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for path in args.input:
                with open_csv_member(path) as (source_name, source):
                    reader = csv.DictReader(source)
                    headers = reader.fieldnames or []
                    columns = resolve_columns(headers)
                    missing_required = [
                        name for name in ("stock_code", "date") if name not in columns
                    ]
                    if missing_required:
                        raise ValueError(
                            f"{source_name}: missing required columns "
                            f"{missing_required}; headers={headers}"
                        )
                    missing_by_file[source_name] = [
                        name for name in ALIASES if name not in columns
                    ]
                    if args.require_p0:
                        has_turnover = any(
                            name in columns for name in ("turnover_total", "turnover_float")
                        )
                        has_shares = any(
                            name in columns
                            for name in ("total_shares", "float_shares", "listed_float_shares")
                        )
                        required_p0 = [
                            name for name in ("close", "volume", "amount") if name not in columns
                        ]
                        if not has_turnover:
                            required_p0.append("turnover_total or turnover_float")
                        if not has_shares:
                            required_p0.append("at least one share-count field")
                        if required_p0:
                            raise ValueError(
                                f"{source_name}: incomplete P0 export; missing {required_p0}"
                            )

                    for line_number, source_row in enumerate(reader, 2):
                        row = {
                            field: as_output_value(field, source_row.get(columns[field], ""))
                            if field in columns
                            else ""
                            for field in OUTPUT_FIELDS
                            if field not in ("market_cap_total_derived", "market_cap_float_derived")
                        }
                        row["company_code_field"] = columns.get("company_code", "")
                        row["source_file"] = source_name
                        row["source_row"] = str(line_number)
                        if not row["stock_code"] or not row["date"]:
                            raise ValueError(f"Missing security/date at source row {line_number}")
                        if len(row["stock_code"]) != 6 or not row["stock_code"].isdigit():
                            raise ValueError(f"Non-six-digit equity code at source row {line_number}")
                        close = numeric(row.get("close", ""))
                        total_shares = numeric(row.get("total_shares", ""))
                        float_shares = numeric(row.get("listed_float_shares", ""))
                        if float_shares is None:
                            float_shares = numeric(row.get("float_shares", ""))
                        row["market_cap_total_derived"] = (
                            f"{close * total_shares:.12g}"
                            if close is not None and total_shares is not None
                            else ""
                        )
                        row["market_cap_float_derived"] = (
                            f"{close * float_shares:.12g}"
                            if close is not None and float_shares is not None
                            else ""
                        )
                        try:
                            keys.execute("INSERT INTO keys VALUES (?,?)", (row["stock_code"], row["date"]))
                        except sqlite3.IntegrityError as exc:
                            raise ValueError(f"Duplicate security/date at source row {line_number}") from exc
                        writer.writerow(row)
                        current_date = row["date"]
                        min_date = current_date if min_date is None else min(min_date, current_date)
                        max_date = current_date if max_date is None else max(max_date, current_date)
                        rows_written += 1
        if not rows_written:
            raise ValueError("No market records were written")
        os.link(temp_output, args.output)
        temp_output.unlink()
    except Exception:
        if temp_output.exists():
            temp_output.unlink()
        raise
    finally:
        keys.close()
        key_path.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "rows_written": rows_written,
                "date_min": min_date,
                "date_max": max_date,
                "missing_columns_by_file": missing_by_file,
                "notes": [
                    "Market capitalizations are derived as raw close times same-day share counts.",
                    "Check the RESSET dictionary for share definitions and turnover units.",
                    "Original row order is preserved; duplicate stock-date keys are rejected, not silently deduplicated.",
                    "CompanyCode and ComCd are retained as separate namespaces; raw security codes are not equity-code fallbacks.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

