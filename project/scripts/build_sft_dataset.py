#!/usr/bin/env python3
"""Convert explicitly human-accepted, source-verified gold to split chat JSONL."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from annotation_validation import DIRECTIONS, label_value, load_pack, preflight_gold, selection
from validation_io import csv_rows, fingerprints, new_private_directory, unchanged, write_json

VALID_DIRECTIONS = set(DIRECTIONS)
SYSTEM_PROMPT = (
    "你是中文上市公司业绩预告事件抽取器。只依据用户提供的公告正文，抽取预期经营或财务业绩方向、"
    "公告明确陈述的原因和支持判断的原文证据。方向不是股价方向。不得推测原文未说明的原因。"
    "只输出一个合法 JSON 对象，字段固定为 direction、causes、evidence；不要输出 Markdown 或额外解释。"
)


def normalized_text(value):
    return " ".join(value.split())


def user_prompt(text):
    return "请按系统要求抽取下列业绩预告。证据必须是正文中的原文片段。\n<公告正文>\n" + text + "\n</公告正文>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="accepted_gold.csv, never an unreviewed draft")
    parser.add_argument("--pack", type=Path, required=True, help="locked original annotation pack")
    parser.add_argument("--stage", choices=["pilot", "final"], default="final")
    parser.add_argument("--output-dir", type=Path, required=True, help="new private directory outside Git")
    args = parser.parse_args()
    pack = load_pack(args.pack)
    before = {**pack["hashes"], **fingerprints([args.input, Path(__file__), Path(__file__).with_name("annotation_validation.py")])}
    rows = list(csv_rows(args.input))
    expected = selection(pack, "pilot_a" if args.stage == "pilot" else "main")
    table = preflight_gold(rows, pack["main"], {r["record_id"] for r in expected})
    unchanged(before)
    output = new_private_directory(args.output_dir)
    written = Counter()
    for split in ("train", "validation", "test"):
        with (output / f"{split}.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
            for row in table.values():
                if row["proposed_split"] != split:
                    continue
                text = row["forecast_text"]
                assistant = json.dumps(label_value(row, require_review=True), ensure_ascii=False, separators=(",", ":"))
                sample = {"source_text": text, "record_id": row["record_id"], "publication_date": row["publication_date"],
                          "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                       {"role": "user", "content": user_prompt(text)},
                                       {"role": "assistant", "content": assistant}]}
                destination.write(json.dumps(sample, ensure_ascii=False) + "\n")
                written[split] += 1
    unchanged(before)
    write_json(output / "dataset_report.json", {"stage": args.stage, "rows_by_split": dict(written),
        "total_written": sum(written.values()), "package_id": pack["package_id"],
        "input_and_code_sha256": before, "direction_vocabulary": sorted(VALID_DIRECTIONS),
        "all_rows_explicitly_human_accepted": True, "prompt_uses_only_announcement_text": True,
        "pilot_is_training_only_not_formal_full_dataset": args.stage == "pilot"})
    print({"stage": args.stage, "rows_by_split": dict(written), "total_written": sum(written.values())})


if __name__ == "__main__":
    main()
