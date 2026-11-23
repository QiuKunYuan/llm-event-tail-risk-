#!/usr/bin/env python3
"""Evaluate JSON extraction on a held-out annotated chat JSONL file."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


def normalize(value: str) -> str:
    return " ".join(value.strip().split()).lower()


from extraction_schema import parse_json_output, strict_json_output


def prf(counts: Counter[str]) -> dict[str, float]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="held-out messages JSONL")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/event_extractor_eval"))
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--compute-dtype", choices=("bf16", "fp16"), default="bf16")
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("Extractor evaluation requires a CUDA GPU")
    if not args.data.is_file():
        raise FileNotFoundError(args.data)
    if args.adapter and not args.adapter.is_dir():
        raise FileNotFoundError(args.adapter)
    if args.compute_dtype == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("GPU does not support bfloat16; use --compute-dtype fp16")

    dtype = torch.bfloat16 if args.compute_dtype == "bf16" else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=dtype,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quantization,
        torch_dtype=dtype,
        device_map="auto",
    )
    if args.adapter:
        model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    direction_labels = ["改善", "恶化", "基本稳定", "混合", "不明确"]
    confusion: Counter[tuple[str, str]] = Counter()
    cause_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    total = parseable = strict_parseable = supported_evidence = predicted_evidence = 0
    predictions_path = args.output_dir / "predictions.jsonl"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.data.open("r", encoding="utf-8") as source, predictions_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as predictions:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            messages = item["messages"]
            if len(messages) < 3 or messages[-1].get("role") != "assistant":
                raise ValueError(f"{args.data}:{line_no}: malformed evaluation messages")
            prompt_messages = messages[:-1]
            gold = json.loads(messages[-1]["content"])
            prompt_ids = tokenizer.apply_chat_template(
                prompt_messages,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
                return_tensors="pt",
            ).to(model.device)
            with torch.inference_mode():
                generated = model.generate(
                    input_ids=prompt_ids,
                    max_new_tokens=args.max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=tokenizer.eos_token_id,
                )
            decoded = tokenizer.decode(generated[0, prompt_ids.shape[1]:], skip_special_tokens=True)
            prediction = parse_json_output(decoded)
            strict_parseable += strict_json_output(decoded) is not None
            total += 1
            if prediction is not None:
                parseable += 1
            gold_direction = gold.get("direction", "不明确")
            predicted_direction = prediction.get("direction", "解析失败") if prediction else "解析失败"
            confusion[(gold_direction, predicted_direction)] += 1

            gold_causes = {normalize(value) for value in gold.get("causes", []) if normalize(value)}
            predicted_causes = {
                normalize(value) for value in (prediction.get("causes", []) if prediction else []) if normalize(value)
            }
            cause_counts["tp"] += len(gold_causes & predicted_causes)
            cause_counts["fp"] += len(predicted_causes - gold_causes)
            cause_counts["fn"] += len(gold_causes - predicted_causes)

            gold_evidence = {normalize(value) for value in gold.get("evidence", []) if normalize(value)}
            predicted_evidence_set = {
                normalize(value) for value in (prediction.get("evidence", []) if prediction else []) if normalize(value)
            }
            evidence_counts["tp"] += len(gold_evidence & predicted_evidence_set)
            evidence_counts["fp"] += len(predicted_evidence_set - gold_evidence)
            evidence_counts["fn"] += len(gold_evidence - predicted_evidence_set)
            body = item.get("source_text")
            if body is None:
                user_content = " ".join(message["content"] for message in prompt_messages if message["role"] == "user")
                match = re.search(r"<公告正文>\s*(.*?)\s*</公告正文>", user_content, re.DOTALL)
                if match is None:
                    raise ValueError("Evaluation requires source_text or marked announcement body")
                body = match.group(1)
            source_text = normalize(body)
            predicted_evidence += len(predicted_evidence_set)
            supported_evidence += sum(span in source_text for span in predicted_evidence_set)
            predictions.write(json.dumps({
                "line": line_no,
                "gold": gold,
                "prediction": prediction,
                "raw_output": decoded,
            }, ensure_ascii=False) + "\n")

    direction_f1 = {}
    for label in direction_labels:
        tp = confusion[(label, label)]
        fp = sum(confusion[(other, label)] for other in direction_labels if other != label)
        fn = sum(confusion[(label, other)] for other in direction_labels if other != label) + confusion[(label, "解析失败")]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        direction_f1[label] = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    report = {
        "model": args.model,
        "adapter": str(args.adapter) if args.adapter else None,
        "gpu": torch.cuda.get_device_name(0),
        "samples": total,
        "strict_json_parse_rate": strict_parseable / total if total else 0.0,
        "permissive_json_parse_rate": parseable / total if total else 0.0,
        "direction_accuracy": sum(confusion[(label, label)] for label in direction_labels) / total if total else 0.0,
        "direction_macro_f1": sum(direction_f1.values()) / len(direction_f1),
        "direction_f1_by_class": direction_f1,
        "cause_exact_phrase_micro": prf(cause_counts),
        "evidence_exact_span_micro": prf(evidence_counts),
        "predicted_evidence_supported_by_input_rate": supported_evidence / predicted_evidence if predicted_evidence else 1.0,
        "predictions": str(predictions_path.resolve()),
        "limitation": "Cause/evidence exact match is strict; report it with manual review and direction macro-F1, not alone.",
    }
    report_path = args.output_dir / "evaluation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
