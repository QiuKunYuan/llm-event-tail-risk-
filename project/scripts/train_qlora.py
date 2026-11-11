#!/usr/bin/env python3
"""Single-GPU QLoRA SFT runner for the manually labeled announcement dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    Trainer,
    TrainingArguments,
)


class ChatJsonlDataset(Dataset):
    def __init__(self, path: Path, tokenizer: Any, max_length: int):
        self.rows: list[dict[str, list[int]]] = []
        self.skipped_too_long = 0
        with path.open("r", encoding="utf-8") as source:
            for line_no, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                item = json.loads(line)
                messages = item.get("messages")
                if not isinstance(messages, list) or len(messages) < 3:
                    raise ValueError(f"{path}:{line_no}: expected system/user/assistant messages")
                if messages[-1].get("role") != "assistant":
                    raise ValueError(f"{path}:{line_no}: last message must be assistant target")
                try:
                    prefix_ids = tokenizer.apply_chat_template(
                        messages[:-1],
                        tokenize=True,
                        add_generation_prompt=True,
                        enable_thinking=False,
                    )
                    full_ids = tokenizer.apply_chat_template(
                        messages,
                        tokenize=True,
                        add_generation_prompt=False,
                        enable_thinking=False,
                    )
                except TypeError as exc:
                    raise RuntimeError(
                        "Tokenizer chat template must support enable_thinking=False; "
                        "use a recent Transformers release with Qwen3 support."
                    ) from exc
                if full_ids[:len(prefix_ids)] != prefix_ids:
                    raise ValueError(f"{path}:{line_no}: chat template prefix mismatch")
                if len(full_ids) > max_length or len(full_ids) <= len(prefix_ids):
                    self.skipped_too_long += 1
                    continue
                labels = [-100] * len(prefix_ids) + full_ids[len(prefix_ids):]
                self.rows.append({"input_ids": full_ids, "labels": labels})
        if not self.rows:
            raise ValueError(f"No usable samples in {path}; check annotation splits and max length")

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.rows[index]


class CausalPaddingCollator:
    def __init__(self, pad_token_id: int, pad_to_multiple_of: int = 8):
        self.pad_token_id = pad_token_id
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, rows: list[dict[str, list[int]]]) -> dict[str, torch.Tensor]:
        max_length = max(len(row["input_ids"]) for row in rows)
        if self.pad_to_multiple_of:
            multiple = self.pad_to_multiple_of
            max_length = ((max_length + multiple - 1) // multiple) * multiple
        input_ids = torch.full((len(rows), max_length), self.pad_token_id, dtype=torch.long)
        attention_mask = torch.zeros((len(rows), max_length), dtype=torch.long)
        labels = torch.full((len(rows), max_length), -100, dtype=torch.long)
        for index, row in enumerate(rows):
            length = len(row["input_ids"])
            input_ids[index, :length] = torch.tensor(row["input_ids"], dtype=torch.long)
            attention_mask[index, :length] = 1
            labels[index, :length] = torch.tensor(row["labels"], dtype=torch.long)
        return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--test", type=Path)
    parser.add_argument("--model", default="Qwen/Qwen3-1.7B")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qwen3-1.7b-qlora"))
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--compute-dtype", choices=("bf16", "fp16"), default="bf16")
    parser.add_argument("--seed", type=int, default=20261110)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("QLoRA runner requires a CUDA GPU; use the rented GPU environment.")
    for path in (args.train, args.validation):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.test and not args.test.is_file():
        raise FileNotFoundError(args.test)

    compute_dtype = torch.bfloat16 if args.compute_dtype == "bf16" else torch.float16
    if args.compute_dtype == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("GPU does not support bfloat16; rerun with --compute-dtype fp16")
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=compute_dtype,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quantization,
        torch_dtype=compute_dtype,
        device_map="auto",
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora = LoraConfig(
        task_type="CAUSAL_LM",
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    train_dataset = ChatJsonlDataset(args.train, tokenizer, args.max_length)
    validation_dataset = ChatJsonlDataset(args.validation, tokenizer, args.max_length)
    test_dataset = ChatJsonlDataset(args.test, tokenizer, args.max_length) if args.test else None
    data_collator = CausalPaddingCollator(tokenizer.pad_token_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation,
        warmup_ratio=0.05,
        weight_decay=0.0,
        lr_scheduler_type="cosine",
        logging_strategy="steps",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        bf16=args.compute_dtype == "bf16",
        fp16=args.compute_dtype == "fp16",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit",
        dataloader_num_workers=2,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        data_collator=data_collator,
    )
    trainer.train()
    trainer.save_model(str(args.output_dir / "best_adapter"))
    tokenizer.save_pretrained(args.output_dir / "best_adapter")
    validation_metrics = trainer.evaluate(metric_key_prefix="validation")
    test_metrics = trainer.evaluate(test_dataset, metric_key_prefix="test") if test_dataset else {}
    report = {
        "model": args.model,
        "gpu": torch.cuda.get_device_name(0),
        "quantization": "4-bit NF4 with double quantization",
        "compute_dtype": args.compute_dtype,
        "lora": {"rank": args.lora_rank, "alpha": args.lora_alpha, "dropout": 0.05},
        "train_rows_used": len(train_dataset),
        "validation_rows_used": len(validation_dataset),
        "test_rows_used": len(test_dataset) if test_dataset else 0,
        "train_rows_skipped_for_length": train_dataset.skipped_too_long,
        "validation_rows_skipped_for_length": validation_dataset.skipped_too_long,
        "test_rows_skipped_for_length": test_dataset.skipped_too_long if test_dataset else 0,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "adapter_dir": str(args.output_dir / "best_adapter"),
    }
    (args.output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
