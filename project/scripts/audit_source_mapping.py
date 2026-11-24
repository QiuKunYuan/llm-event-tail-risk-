"""Check corrected namespaces without relabeling; prepare ten private disclosure cases."""
import argparse
import csv
import hashlib
import itertools
from collections import Counter
from pathlib import Path

from annotation_workbook_io import write_workbook
from validation_io import csv_rows, fingerprints, new_private_directory, number, unchanged, write_json


def compare_panels(old_path, new_path, allowed_changes=()):
    counts = Counter()
    n = 0
    for n, (old, new) in enumerate(itertools.zip_longest(csv_rows(old_path), csv_rows(new_path)), 1):
        if old is None or new is None:
            raise ValueError("Old/new panel length changed")
        for field, value in old.items():
            if new.get(field) != value:
                counts[field] += 1
                if field not in allowed_changes:
                    raise ValueError(f"Unexpected old/new field change: {field}, row {n}")
    return {"rows": n, "changed_fields": dict(counts), "all_other_fields_identical": True}


def observe_mapping(path):
    pairs, bridges = {}, {}
    counts = Counter()
    for row in csv_rows(path):
        counts["rows"] += 1
        company, stock, day = row["company_code"], row["stock_code"], row["date"]
        if company and not row["company_code_field"].casefold().endswith("_comcd"):
            raise ValueError("Mixed company-code namespace remains")
        if not company or not stock:
            counts["missing_company_or_stock"] += 1
            continue
        key = (company, stock)
        value = pairs.setdefault(key, {"first": day, "last": day, "rows": 0})
        value["first"], value["last"] = min(value["first"], day), max(value["last"], day)
        value["rows"] += 1
        alternate = row["raw_company_code"]
        if alternate:
            counts["alternate_code_nonempty"] += 1
            counts["two_namespaces_differ"] += alternate != company
            bridges[(company, alternate)] = bridges.get((company, alternate), 0) + 1
    companies, stocks = {}, {}
    for company, stock in pairs:
        companies.setdefault(company, set()).add(stock)
        stocks.setdefault(stock, set()).add(company)
    counts.update({"listed_company_codes": len(companies), "securities": len(stocks),
                   "observed_pairs": len(pairs), "stocks_with_multiple_comcd": sum(len(v) > 1 for v in stocks.values()),
                   "comcd_with_multiple_stock_codes": sum(len(v) > 1 for v in companies.values()),
                   "namespace_bridge_pairs": len(bridges)})
    return pairs, companies, bridges, dict(counts)


def candidate_status(row, pairs, companies):
    company, stock = row.get("company_code", ""), row.get("stock_code", "")
    if not company:
        return "missing_comcd"
    if not stock:
        options = companies.get(company, set())
        return "missing_stock_one_observed_candidate_NOT_backfilled" if len(options) == 1 else "missing_stock_ambiguous_or_unseen"
    pair = pairs.get((company, stock))
    if pair is None:
        return "pair_not_observed_in_DRET"
    day = row["publication_date"]
    return "observed_pair_candidate_NOT_certified" if pair["first"] <= day <= pair["last"] else "outside_observed_support_NOT_listing_dates"


def write_records(path, fields, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-returns", type=Path, required=True)
    parser.add_argument("--returns", type=Path, required=True)
    parser.add_argument("--old-shareholders", type=Path, required=True)
    parser.add_argument("--shareholders", type=Path, required=True)
    parser.add_argument("--earnings", type=Path, required=True)
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    before = fingerprints([args.old_returns, args.returns, args.old_shareholders, args.shareholders,
                           args.earnings, args.factors, Path(__file__)])
    output = new_private_directory(args.output_dir)
    returns_comparison = compare_panels(args.old_returns, args.returns, {"company_code"})
    shareholders_comparison = compare_panels(args.old_shareholders, args.shareholders)
    pairs, companies, bridges, mapping = observe_mapping(args.returns)
    write_records(output / "company_security_observed_crosswalk.csv",
                  ["comcd", "stock_code", "first_observed", "last_observed", "observations", "interval_status"],
                  ({"comcd": c, "stock_code": s, "first_observed": v["first"], "last_observed": v["last"], "observations": v["rows"], "interval_status": "observed_support_only_NOT_certified_listing_interval"} for (c, s), v in sorted(pairs.items())))
    write_records(output / "two_namespace_observed_bridge.csv", ["comcd", "raw_CompanyCode", "observations", "status"],
                  ({"comcd": c, "raw_CompanyCode": alt, "observations": n, "status": "observed_correspondence_not_alias_equivalence"} for (c, alt), n in sorted(bridges.items())))
    match_counts, shareholder_counts = {}, Counter()
    sample_keys = {}
    sample_fields = None
    for name, path in (("earnings", args.earnings), ("shareholders", args.shareholders)):
        matches = Counter()
        for row in csv_rows(path):
            if name == "shareholders":
                sample_fields = list(row)
                shareholder_counts["rows"] += 1
                shareholder_counts["holder_id_empty"] += not row["holder_id"]
                shareholder_counts["holder_type_code_empty"] += not row["holder_type_code"]
                for field in ("info_type_code", "original_disclosure_flag", "holder_series_by_type", "affiliated_security_code", "disclosure_id"):
                    shareholder_counts[field + "_nonempty"] += bool(row[field])
            if not "2017-02-06" <= row["publication_date"] <= "2024-07-24":
                continue
            matches[candidate_status(row, pairs, companies)] += 1
            if name == "shareholders" and row["stock_code"] and 1 <= number(row["holder_rank"]) <= 10:
                key = tuple(row[k] for k in ("stock_code", "report_date", "publication_date", "info_source"))
                digest = hashlib.sha256("\x1f".join(key).encode()).hexdigest()
                if digest in sample_keys:
                    continue
                if len(sample_keys) < 10 or digest < max(sample_keys):
                    sample_keys[digest] = key
                    if len(sample_keys) > 10:
                        del sample_keys[max(sample_keys)]
        match_counts[name] = dict(matches)
    chosen = set(sample_keys.values())
    sample_rows = [row for row in csv_rows(args.shareholders)
                   if tuple(row[k] for k in ("stock_code", "report_date", "publication_date", "info_source")) in chosen]
    write_records(output / "ten_disclosure_groups_raw_rows.csv", sample_fields, sample_rows)
    tasks = [["样本组", "公司证券代码", "报告截止日", "数据公开日", "数据来源", "原版报告文件/URL", "名单类型（有证据才填）", "名单页码", "更正及更正公开日期", "疑问"]]
    for i, key in enumerate(sorted(chosen), 1):
        tasks.append([str(i), *key, "", "", "", "", ""])
    write_workbook(output / "股东补证_10组任务.xlsx", {"补证任务": tasks,
        "现有原行": [sample_fields] + [[r[k] for k in sample_fields] for r in sample_rows],
        "说明": [["注意", "含授权原行，私有返回，不公开上传"], ["目的", "按公司/报告期定位原版报告，查看总股东/流通股东名单及更正，填写证据页码。"],
                  ["边界", "10组只是诊断案例，不认证全市场；正式仍需100组/100主体。名单类型不明就写待确认，不任意拆重复排名。"]]},
                   editable={"补证任务": set(range(5, 10))})
    unchanged(before)
    report = {"returns_comparison": returns_comparison, "shareholders_comparison": shareholders_comparison,
              "corrected_mapping": mapping, "mapping_candidates_only": match_counts,
              "shareholder_fields": dict(shareholder_counts), "diagnostic_groups": len(chosen),
              "diagnostic_raw_rows": len(sample_rows), "main_graph_ready": False,
              "no_missing_stock_codes_backfilled": True, "observed_ranges_are_not_listing_intervals": True,
              "label_dependency_fields_unchanged": True, "input_and_code_sha256": before}
    write_json(output / "mapping_report.json", report)
    print({k: v for k, v in report.items() if k not in {"input_and_code_sha256", "mapping_candidates_only"}})


if __name__ == "__main__":
    main()
