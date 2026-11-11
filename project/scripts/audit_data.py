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

    gaps: list[dict[str, str]] = []
    if not market_archives:
        gaps.append(
            {
                "priority": "P0",
                "item": "日股票综合行情 DRESSTK",
                "missing": "本地没有对应行情导出；缺价格、成交量/额、换手率、股数等字段。",
                "action": "补 2014-01 至 2026-01-15，覆盖预热期、预测期和未来五日标签。",
            }
        )
    if not turnover_value_fields:
        gaps.append(
            {
                "priority": "P0",
                "item": "有数值列的日换手率",
                "missing": "现有 DTURNR 工作簿审计到的字段没有 DFulTurnR/DTrdTurnR。",
                "action": "从 DRESSTK 选换手率字段；若该表不含，再重新导出含数值列的 DTURNR。",
            }
        )
    if not covers(return_max, "2026-01-15"):
        gaps.append(
            {
                "priority": "P0",
                "item": "个股收益尾部日期",
                "missing": f"本地实际最大日期为 {return_max or '未知'}，标签输入目标至少到 2026-01-15。",
                "action": "补实际缺失日期；若 DRESSTK 含 Dret/Daret，可用来补齐并交叉核验。",
            }
        )
    if not covers(factor_max, "2026-01-15"):
        gaps.append(
            {
                "priority": "P0",
                "item": "日三因子尾部日期",
                "missing": f"本地实际最大日期为 {factor_max or '未知'}。",
                "action": "只补到 2026-01-15，不重复下载已有的 2015—2025 因子。",
            }
        )
    if not basic_archives:
        gaps.append(
            {
                "priority": "P1核对",
                "item": "股票基本信息/历史证券映射",
                "missing": "没有独立的基本信息压缩包；当前有上市状态变更表。",
                "action": "先确认变更表是否含上市/退市日期及代码/简称生效期，缺什么再补什么。",
            }
        )
    if not finance_archives:
        gaps.append(
            {
                "priority": "可选",
                "item": "独立财务报表",
                "missing": "本地目录没有独立财务报表/财务指标压缩包。",
                "action": "仅当加入财务特征或自行构造账面市值比时下载，并保留信息发布日期。",
            }
        )

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

