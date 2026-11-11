#!/usr/bin/env python3
"""Convert completed manual annotation CSVs into temporal-split chat JSONL."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALID_DIRECTIONS = {"改善", "恶化", "基本稳定", "混合", "不明确"}
SYSTEM_PROMPT = (
    "你是中文上市公司业绩预告事件抽取器。只依据用户提供的公告正文，抽取预期经营或财务业绩方向、"
    "公告明确陈述的原因和支持判断的原文证据。方向不是股价方向。不得推测原文未说明的原因。"
    "只输出一个合法 JSON 对象，字段固定为 direction、causes、evidence；不要输出 Markdown 或额外解释。"
)


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def parse_array(value: str, field: str, record_id: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{record_id}: {field} must be valid JSON array: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{record_id}: {field} must be a JSON array of strings")
    return parsed


def user_prompt(text: str) -> str:
    return (
        "请按系统要求抽取下列业绩预告。证据必须是正文中的原文片段。\n"
        "<公告正文>\n" + text + "\n</公告正文>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="adjudicated annotator A CSV")
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "outputs" / "sft_dataset",
    )
    args = parser.parse_args()
    source_path = args.input.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    written: Counter[str] = Counter()
    seen_ids: set[str] = set()
    skipped_adjudication = 0
    with source_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {
            "record_id", "publication_date", "forecast_text", "proposed_split",
            "direction", "causes_json", "evidence_json", "needs_adjudication",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Annotation CSV missing columns: {sorted(missing)}")
        outputs = {
            split: (args.output_dir / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n")
            for split in ("train", "validation", "test")
        }
        try:
            for row in reader:
                record_id = row["record_id"].strip()
                if not record_id or record_id in seen_ids:
                    raise ValueError(f"Missing or duplicate record_id: {record_id!r}")
                seen_ids.add(record_id)
                split = row["proposed_split"].strip()
                if split not in outputs:
                    raise ValueError(f"{record_id}: unsupported split {split!r}")
                if row["needs_adjudication"].strip().lower() in {"yes", "y", "是", "待裁决"}:
                    skipped_adjudication += 1
                    continue
                direction = row["direction"].strip()
                if direction not in VALID_DIRECTIONS:
                    raise ValueError(f"{record_id}: invalid or empty direction {direction!r}")
                text = row["forecast_text"].strip()
                causes = parse_array(row["causes_json"], "causes_json", record_id)
                evidence = parse_array(row["evidence_json"], "evidence_json", record_id)
                if not text or not evidence:
                    raise ValueError(f"{record_id}: text and at least one evidence span are required")
                normalized_body = normalized_text(text)
                for span in evidence:
                    if not span.strip() or normalized_text(span) not in normalized_body:
                        raise ValueError(f"{record_id}: evidence span is not found in source text")

                assistant = json.dumps(
                    {"direction": direction, "causes": causes, "evidence": evidence},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                sample = {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt(text)},
                        {"role": "assistant", "content": assistant},
                    ]
                }
                outputs[split].write(json.dumps(sample, ensure_ascii=False) + "\n")
                written[split] += 1
        finally:
            for stream in outputs.values():
                stream.close()

    report = {
        "source": str(source_path),
        "output_dir": str(args.output_dir.resolve()),
        "rows_by_split": dict(written),
        "rows_skipped_for_adjudication": skipped_adjudication,
        "total_written": sum(written.values()),
        "direction_vocabulary": sorted(VALID_DIRECTIONS),
        "prompt_uses_only_announcement_text": True,
    }
    report_path = args.output_dir / "dataset_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
