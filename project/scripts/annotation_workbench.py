"""Private Chinese Excel annotation kit, independent agreement and human-gold gates."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from annotation_validation import (DIRECTIONS, SOURCE_FIELDS, VERSION, label_value,
                                   load_pack, preflight_gold, selection, source_matches, unique_rows)
from annotation_workbook_io import read_workbook, write_workbook
from prepare_announcement_annotations import ANNOTATION_FIELDS
from validation_io import (csv_rows, fingerprints, new_private_directory, sha256,
                           unchanged, write_json)

HEADERS = ["样本编号（勿改）", "公告正文（勿改）", "业绩方向", "原因状态", "明确原因（每行一条）",
           "原文证据1", "原文证据2", "原文证据3", "置信度", "填写状态", "疑问或备注", "耗时秒（可空）",
           "事件族编号（勿改）", "预测对象原码（勿改）", "证券代码（勿改）", "公开日期（勿改）", "报告期（勿改）", "数据划分（勿改）"]
SOURCE_COLUMNS = {"record_id": 0, "forecast_text": 1, "family_id": 12, "forecast_object": 13,
                  "stock_code": 14, "publication_date": 15, "report_date": 16, "proposed_split": 17}
EXTRA_FIELDS = ["work_status", "review_status", "reviewer", "annotator_role", "annotation_version",
                "selection", "elapsed_seconds", "independent_a_sha256", "independent_b_sha256"]
CSV_FIELDS = ANNOTATION_FIELDS + EXTRA_FIELDS
FILES = {"pilot_a": "先做_200条_A.xlsx", "pilot_b": "独立试标_42条_B.xlsx",
         "remaining_a": "规则冻结后_余1300条_A.xlsx", "remaining_b": "规则冻结后_余258条_B.xlsx"}
DROPDOWNS = {2: DIRECTIONS, 3: ("已说明", "未提及", "待讨论"), 8: ("高", "中", "低"),
             9: ("未完成", "已填写", "待讨论")}
GUIDE_ROWS = [
    ["操作", "说明"],
    ["只填C—L列", "先完整读B列正文；不查后续股价，不预测尾部风险，不看另一人的答案。原文和编号列不能改。"],
    ["方向", "改善/恶化/基本稳定/混合/不明确。仍亏损但亏损收窄=改善；盈利但同比下降=恶化。没有原因不代表方向不明确。"],
    ["原因状态", "有明确原因选已说明，每行一条短语（Alt+Enter换行）；没有说原因选未提及并留原因格为空。不猜行业背景。"],
    ["证据", "F—H列复制1—3段正文原话，不改写，不引用后来新闻或数据库元数据。"],
    ["填写状态", "未完成不是不明确。看完并填完选已填写；低置信度或看不懂选待讨论并写疑问。"],
    ["独立标注", "A先做200；B独立做已有双标中重合的42。规则冻结后A做剩1300、B做剩258。不能把200重复加到1500里。"],
    ["耗时", "可填非负秒数；不用计时器也可以留空。置信度是人工主观判断，不是概率。"],
    ["保存返回", "保留.xlsx，不另存为旧.xls，不用宏/公式，不删行。受控私有方式返回；不要上传公共GitHub或分享公开链接。"],
    ["放行", "已填写只表示草稿完成，不是训练金标。先检错、保留独立原稿、计算一致率、裁决，再人工确认复核。"],
    ["显示", "正文完整存储；若行高不足可选中B单元格看编辑栏/调整行高。冻结首行，必要时横向滚动。没有做Office视觉验收。"],
]


def table_cells(row):
    values = [""] * len(HEADERS)
    for field, c in SOURCE_COLUMNS.items():
        values[c] = row.get(field, "")
    values[9] = "未完成"
    return values


def save_annotation_book(path, rows, metadata, extra_headers=None, extra_rows=None):
    headers = HEADERS + (extra_headers or [])
    data = [headers]
    for row in rows:
        data.append(table_cells(row) + (extra_rows.get(row["record_id"], []) if extra_rows else []))
    write_workbook(path, {"说明": GUIDE_ROWS, "待标注": data, "包信息": [["字段", "值"]] + [[k, v] for k, v in metadata.items()]},
                   editable={"待标注": set(range(2, 12))}, dropdowns={"待标注": DROPDOWNS})


def write_csv(path, rows, fields=CSV_FIELDS):
    with path.open("x", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def metadata(pack, name, role, extra=None):
    return {"version": VERSION, "package_id": pack["package_id"], "selection": name,
            "role": role, **(extra or {})}


def export_kit(pack, output):
    manifests = {}
    for name, filename in FILES.items():
        rows = selection(pack, name)
        role = "A" if name.endswith("a") else "B"
        save_annotation_book(output / filename, rows, metadata(pack, name, role))
        manifests[filename] = {"selection": name, "rows": len(rows), "role": role, "sha256": sha256(output / filename)}
    write_json(output / "kit_manifest.json", {"version": VERSION, "package_id": pack["package_id"],
        "source_sha256": pack["hashes"], "workbooks": manifests,
        "labels_filled": 0, "human_gold_count": 0, "private_transfer_completed": False,
        "pilot_double_overlap": 42, "no_new_sampling": True})
    return {"workbook_rows": [v["rows"] for v in manifests.values()], "human_gold_count": 0}


def read_submission(path, pack):
    rows = list(csv_rows(path))
    table = unique_rows(rows)
    for key, row in table.items():
        if key not in pack["main"] or not source_matches(row, pack["main"][key]):
            raise ValueError("Submission changed source or contains unknown IDs")
        if row.get("annotation_version") != VERSION:
            raise ValueError("Stale/unknown annotation version")
    return table


def import_book(path, pack):
    sheets = read_workbook(path)
    if not {"包信息", "待标注"} <= set(sheets) or not sheets["待标注"]:
        raise ValueError("Missing annotation worksheet or package metadata")
    metadata_rows = sheets["包信息"][1:]
    if any(len(row) != 2 for row in metadata_rows) or len({row[0] for row in metadata_rows}) != len(metadata_rows):
        raise ValueError("Invalid/duplicate package metadata fields")
    info = dict(metadata_rows)
    if info.get("version") != VERSION or info.get("package_id") != pack["package_id"]:
        raise ValueError("Workbook does not belong to the locked package/version")
    name, role = info.get("selection"), info.get("role")
    if role not in ("A", "B", "R"):
        raise ValueError("Unknown workbook role")
    allowed = {"A": {"pilot_a", "remaining_a"}, "B": {"pilot_b", "remaining_b"}, "R": {"main", "pilot_a"}}
    if name not in allowed[role]:
        raise ValueError("Workbook role and member selection conflict")
    expected = unique_rows(selection(pack, name))
    data = sheets["待标注"]
    if data[0][:len(HEADERS)] != HEADERS:
        raise ValueError("Changed/stale workbook column schema")
    rows, issues, counts = [], [], Counter()
    for line in data[1:]:
        if not any(line):
            continue
        values = line + [""] * max(0, len(HEADERS) - len(line))
        row = {field: values[c] for field, c in SOURCE_COLUMNS.items()}
        key = row["record_id"]
        if key not in expected or not source_matches(row, expected[key]):
            raise ValueError("Workbook ID/source field modified")
        state = values[9] or "未完成"
        if state not in DROPDOWNS[9]:
            raise ValueError("Unknown work status; do not silently treat it as complete")
        row.update({"direction": values[2], "causes_json": json.dumps([s.strip() for s in values[4].splitlines() if s.strip()], ensure_ascii=False) if values[3] == "已说明" else "[]" if values[3] == "未提及" else "",
                    "evidence_json": json.dumps([s for s in values[5:8] if s], ensure_ascii=False),
                    "annotator_confidence": values[8], "annotator_note": values[10],
                    "work_status": state, "review_status": "pending", "reviewer": "",
                    "needs_adjudication": "yes" if state != "已填写" or values[8] == "低" or values[3] == "待讨论" else "no",
                    "annotator_role": role, "annotation_version": VERSION, "selection": name,
                    "elapsed_seconds": values[11], "independent_a_sha256": info.get("a_sha256", ""),
                    "independent_b_sha256": info.get("b_sha256", "")})
        errors = []
        if state == "未完成":
            counts["unfinished"] += 1
        else:
            try:
                if values[3] not in ("已说明", "未提及", "待讨论"):
                    raise ValueError("原因状态未填")
                if values[3] == "未提及" and values[4].strip():
                    raise ValueError("选了未提及但原因格有内容")
                if values[3] == "已说明" and not values[4].strip():
                    raise ValueError("选了已说明但没有原因短语")
                if values[8] not in DROPDOWNS[8]:
                    raise ValueError("置信度须选高/中/低")
                if values[11] and (not float(values[11]) >= 0 or not float(values[11]) < float("inf")):
                    raise ValueError("耗时须为非负有限秒数")
                if row["needs_adjudication"] == "yes" and not values[10].strip():
                    raise ValueError("待讨论/低置信度必须写疑问")
                label_value(row)
            except ValueError as exc:
                errors.append(str(exc))
            counts["invalid_completed" if errors else "valid_draft"] += 1
        if row["needs_adjudication"] == "yes" and state != "未完成":
            counts["needs_discussion"] += 1
        if errors:
            row["needs_adjudication"] = "yes"
        if errors or state == "未完成":
            issues.append({"record_id": key, "问题": "; ".join(errors) if errors else "尚未标注"})
        rows.append(row)
    if set(unique_rows(rows)) != set(expected):
        raise ValueError("Workbook missing/duplicated member IDs")
    return rows, issues, {"rows": len(rows), "counts": dict(counts), "human_gold_count": 0, "role": role, "selection": name}


def agreement(a, b):
    pairs, disagreements = [], []
    for key, right in b.items():
        left = a[key]
        reason = []
        try:
            if left.get("work_status") not in ("已填写", "待讨论") or right.get("work_status") not in ("已填写", "待讨论"):
                raise ValueError("尚未独立填完")
            first, second = label_value(left), label_value(right)
            pairs.append((first["direction"], second["direction"]))
            for field in ("direction", "causes", "evidence"):
                if first[field] != second[field]:
                    reason.append(field + "不同（短语/引文不同不自动等于语义错误）")
            if left.get("needs_adjudication") != "no" or right.get("needs_adjudication") != "no":
                reason.append("人工待讨论")
        except ValueError as exc:
            reason.append(str(exc))
        if reason:
            disagreements.append({"record_id": key, "问题": "; ".join(reason), "A方向": left.get("direction", ""), "B方向": right.get("direction", "")})
    n = len(pairs)
    actual = sum(x == y for x, y in pairs) / n if n else None
    left_counts = Counter(x for x, _ in pairs)
    right_counts = Counter(y for _, y in pairs)
    expected = sum(left_counts[d] * right_counts[d] for d in DIRECTIONS) / (n * n) if n else None
    kappa = (actual - expected) / (1 - expected) if n and expected < 1 else None
    return {"expected_pairs": len(b), "valid_independent_pairs": n, "direction_agreement": actual,
            "cohens_kappa": kappa, "kappa_undefined_if_no_pairs_or_single_constant_class": True,
            "training_pilot_only": all(r["proposed_split"] == "train" for r in b.values())}, disagreements


def stage_members(pack, stage):
    return (set(pack["pilot"]), set(pack["pilot"]) & set(pack["double"])) if stage == "pilot" else (set(pack["main"]), set(pack["double"]))


def check_stage(a, b, pack, stage):
    a_ids, b_ids = stage_members(pack, stage)
    if set(a) != a_ids or set(b) != b_ids:
        raise ValueError("Stage must use exact locked A/B membership")
    if any(r.get("annotator_role") != "A" for r in a.values()) or any(r.get("annotator_role") != "B" for r in b.values()):
        raise ValueError("Do not mix A/B independent submissions")


def approve(a, b, resolutions, pack, stage, reviewer, a_hash, b_hash):
    check_stage(a, b, pack, stage)
    independent_report, differences = agreement(a, b)
    if independent_report["valid_independent_pairs"] != len(b):
        raise ValueError("Complete the locked independent A/B labels before adjudication/gold")
    flagged = {r["record_id"] for r in differences}
    result = []
    for key, left in a.items():
        needs_resolution = key in flagged or left.get("needs_adjudication") != "no"
        if needs_resolution:
            if key not in resolutions:
                raise ValueError("Unresolved disagreement/low-confidence record")
            row = dict(resolutions[key])
            if row.get("annotator_role") != "R" or row.get("work_status") != "已填写" or row.get("needs_adjudication") != "no" or not row.get("annotator_note", "").strip():
                raise ValueError("Resolution must be completed with an explicit human explanation")
            if row.get("independent_a_sha256") != a_hash or row.get("independent_b_sha256") != b_hash:
                raise ValueError("Resolution refers to different independent submissions")
        else:
            if left.get("work_status") != "已填写":
                raise ValueError("Unfinished single-annotator record")
            row = dict(left)
        label_value(row)
        row.update({"review_status": "accepted", "needs_adjudication": "no", "reviewer": reviewer})
        result.append(row)
    preflight_gold(result, pack["main"], stage_members(pack, stage)[0])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "import", "merge", "compare", "approve"])
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, nargs="+")
    parser.add_argument("--a", type=Path)
    parser.add_argument("--b", type=Path)
    parser.add_argument("--resolution", type=Path)
    parser.add_argument("--stage", choices=["pilot", "final"], default="pilot")
    parser.add_argument("--reviewer")
    parser.add_argument("--confirm-human-review", action="store_true")
    args = parser.parse_args()
    pack = load_pack(args.pack)
    paths = [p for p in (args.input or []) + [args.a, args.b, args.resolution] if p is not None]
    before = {**pack["hashes"], **fingerprints(paths), **fingerprints([Path(__file__), Path(__file__).with_name("annotation_validation.py"), Path(__file__).with_name("annotation_workbook_io.py")])}
    if args.command == "approve" and (not args.confirm_human_review or not (args.reviewer or "").strip()):
        raise ValueError("Require an identified human reviewer and explicit human-review confirmation")
    output = new_private_directory(args.output_dir)
    if args.command == "export":
        report = export_kit(pack, output)
    elif args.command == "import":
        if not args.input or len(args.input) != 1:
            raise ValueError("Import exactly one workbook")
        rows, issues, report = import_book(args.input[0], pack)
        write_csv(output / "draft_annotations.csv", rows)
        write_csv(output / "待修复清单.csv", issues, ["record_id", "问题"])
    elif args.command == "merge":
        if not args.input:
            raise ValueError("Merge needs completed subset submissions")
        rows = [r for path in args.input for r in read_submission(path, pack).values()]
        unique_rows(rows)
        roles = {r["annotator_role"] for r in rows}
        if len(roles) != 1:
            raise ValueError("Never merge A and B independent answers")
        write_csv(output / "merged_annotations.csv", rows)
        report = {"rows": len(rows), "roles": sorted(roles), "human_gold_count": 0}
    else:
        if not args.a or not args.b:
            raise ValueError("Comparison/approval requires independent A and B CSVs")
        a, b = read_submission(args.a, pack), read_submission(args.b, pack)
        check_stage(a, b, pack, args.stage)
        report, differences = agreement(a, b)
        if args.command == "compare":
            write_csv(output / "独立分歧清单.csv", differences, ["record_id", "问题", "A方向", "B方向"])
            extra = ["A原方向", "A原原因", "A原证据", "B原方向", "B原原因", "B原证据"]
            cells = {key: [a[key].get(f, "") for f in ("direction", "causes_json", "evidence_json")] + [b.get(key, {}).get(f, "") for f in ("direction", "causes_json", "evidence_json")] for key in a}
            selected = "pilot_a" if args.stage == "pilot" else "main"
            save_annotation_book(output / "裁决与复核.xlsx", list(a.values()), metadata(pack, selected, "R", {"a_sha256": sha256(args.a), "b_sha256": sha256(args.b)}), extra, cells)
            report["human_gold_count"] = 0
        else:
            resolutions = read_submission(args.resolution, pack) if args.resolution else {}
            rows = approve(a, b, resolutions, pack, args.stage, args.reviewer.strip(), sha256(args.a), sha256(args.b))
            write_csv(output / "accepted_gold.csv", rows)
            report.update({"human_gold_count": len(rows), "human_reviewer": args.reviewer,
                           "human_review_assertion_not_algorithmic_semantic_verification": True})
    unchanged(before)
    report.update({"command": args.command, "stage": args.stage, "version": VERSION, "package_id": pack["package_id"], "input_and_code_sha256": before})
    write_json(output / "workbench_report.json", report)
    print({k: v for k, v in report.items() if k not in {"input_and_code_sha256", "package_id"}})


if __name__ == "__main__":
    main()
