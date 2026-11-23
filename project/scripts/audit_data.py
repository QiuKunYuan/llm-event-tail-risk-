#!/usr/bin/env python3
"""Audit the known local RESSET exports and report concrete acquisition gaps."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


DEFAULT_DATA_ROOT = Path("/home/admin/workspace/RESSET论文数据")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    return value if isinstance(value, dict) else {}


def data_max_date(report: dict, table: str, date_key: str = "date") -> str | None:
    return (
        report.get("tables", {})
        .get(table, {})
        .get("date_ranges", {})
        .get(date_key, {})
        .get("max")
    )


def covers(actual: str | None, expected: str) -> bool:
    if not actual:
        return False
    try:
        return date.fromisoformat(actual[:10]) >= date.fromisoformat(expected)
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "data_readiness.json",
    )
    args = parser.parse_args()
    root = args.data_root.expanduser().resolve()
    prep = load_json(root / "processed" / "preparation_report.json")
    archive_audit = load_json(root / "data_audit.json")

    returns = prep.get("tables", {}).get("daily_returns", {})
    factors = prep.get("tables", {}).get("daily_factors", {})
    labels = load_json(root / "processed" / "tail_risk_labels_report.json")
    return_max = data_max_date(prep, "daily_returns")
    factor_max = data_max_date(prep, "daily_factors")
    label_max = labels.get("date_range", {}).get("max")

    archives = [path for path in root.rglob("*.zip") if path.is_file()]
    market_archives = [
        str(path.relative_to(root))
        for path in archives
        if "dresstk" in path.name.lower() or "日股票综合" in path.name
    ]
    basic_archives = [
        str(path.relative_to(root))
        for path in archives
        if any(token in path.name for token in ("基本信息", "基本情况", "证券资料"))
    ]
    finance_archives = [
        str(path.relative_to(root))
        for path in archives
        if any(token in path.name for token in ("财务报表", "财务指标", "资产负债"))
    ]

    turnover_workbooks = [
        workbook
        for workbook in archive_audit.get("workbooks", [])
        if "DTURNR" in workbook.get("workbook", "").upper()
    ]
    turnover_headers = sorted(
        {
            header
            for workbook in turnover_workbooks
            for header in workbook.get("headers", [])
        }
    )
    turnover_value_fields = [
        header
        for header in turnover_headers
        if "DFulTurnR" in header or "DTrdTurnR" in header
    ]

    # The thesis scope is fixed to available return data through July 2024.
    gaps: list[dict[str, str]] = []
    for maximum, name in [(return_max, "个股收益"), (factor_max, "日三因子")]:
        if not covers(maximum, "2024-07-31"):
            gaps.append({"priority": "P0", "item": name + "标签输入覆盖",
                         "missing": f"实际最大日期为 {maximum or '未知'}",
                         "action": "核对现有文件与2024-07-31标签截止，禁止按ZIP名称推定日期。"})
    # Price, turnover and financial statements are outside the fixed input contract.
    result = {
        "data_root": str(root),
        "daily_returns": {
            "rows": returns.get("rows_written"),
            "date_min": returns.get("date_ranges", {}).get("date", {}).get("min"),
            "date_max": return_max,
        },
        "daily_factors": {
            "rows": factors.get("rows_written"),
            "date_min": factors.get("date_ranges", {}).get("date", {}).get("min"),
            "date_max": factor_max,
        },
        "existing_labels": {
            "rows": labels.get("label_rows"),
            "date_min": labels.get("date_range", {}).get("min"),
            "date_max": label_max,
        },
        "dresstk_archives": market_archives,
        "turnover_value_fields_found": turnover_value_fields,
        "basic_info_archives": basic_archives,
        "finance_archives": finance_archives,
        "gaps": gaps,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

