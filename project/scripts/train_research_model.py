"""Strict-contract SYNTHETIC CPU training, calibration, prediction-universe export.

No real financial optimization is enabled. Future data-team assemblers must emit
risk_batch_contract v1 artifacts, not an unverified NPZ/max-date manifest. Run
--synthetic-smoke --output-dir OUTSIDE_GIT to generate one valid daily demo and a
small chronological train/selection/calibration/test set. --validate-only accepts
candidate artifacts without training. Checkpoints bind optimizer, RNG, position,
configuration, code, index/cache and every input hash for exact resume.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import random

import numpy as np
import pandas as pd
import torch
from research_model import DisclosureModel
from risk_batch_contract import RiskBatchContract, ROLES, SCHEMA
from probability_calibration import BIN_COLUMNS, PROBABILITY_COLUMNS, cumulative_probabilities, fit_temperature, select_threshold, temperature_scale
from evaluate_predictions import evaluate_risk
from validation_io import fingerprints, new_private_directory, sha256, write_json


def synthetic_batch(day="2020-01-06", unknown=False):
    """Small deterministic synthetic tensors, including two unsupervised neighbors."""
    cutoff = date.fromisoformat(day).toordinal()
    rng = np.random.default_rng(cutoff)
    n, t, k = 8, 5, 2
    a = {"seq": rng.normal(size=(n, t, 3)).astype("float32"),
         "market_seq": rng.normal(size=(1, t, 2)).astype("float32"),
         "sector_seq": rng.normal(size=(2, t, 2)).astype("float32"),
         "seq_mask": np.ones((n, t, 3), bool), "market_mask": np.ones((1, t, 2), bool),
         "sector_mask": np.ones((2, t, 2), bool), "market_index": np.zeros(n, "int64"),
         "sector_index": np.arange(n, dtype="int64") % 2,
         "sector_key": np.asarray(["industry-A", "industry-B"], dtype="U10"),
         "stock_code": np.asarray([f"{i + 1:06d}" for i in range(n)], dtype="U6"),
         "events": rng.normal(size=(n, k, 8)).astype("float32"), "event_mask": np.ones((n, k), bool),
         "body_available": np.ones((n, k), bool), "event_json_valid": np.ones((n, k), bool),
         "body_quality": np.ones((n, k), "float32"), "evidence_quality": np.ones((n, k), "float32"),
         "event_age": np.ones((n, k), "float32"),
         "predicted_fields": rng.normal(size=(n, k, 2)).astype("float32"), "field_mask": np.ones((n, k, 2), bool),
         "edges": np.stack((np.arange(n), np.arange(n) // 2)).astype("int64"),
         "edge_share": np.full(n, .1, "float32"), "edge_age": np.ones(n, "float32"),
         "edge_accepted_complete": np.ones(n, bool), "graph_age": np.ones(n, "float32"),
         "graph_quality": np.ones(n, "float32"), "prediction_member": np.arange(n) < 6,
         "label_bin": np.asarray([0, 1, 2, 3, 0, 1, -1, -1], "int64"),
         "outcome_status": np.asarray(["known"] * 6 + ["neighbor_not_target"] * 2, dtype="U40"),
         "outcome_end_ordinal": np.asarray([cutoff + 7] * 6 + [0, 0], "int64")}
    if unknown:
        a["label_bin"][5] = -1
        a["outcome_status"][5] = "administrative_truncation"
        # Expected endpoint can exist even though the future outcome is unknown.
    a["unknown_reason"] = np.asarray([""] * n, dtype="U40")
    if unknown:
        a["unknown_reason"][5] = "administrative_truncation"
    a["observed_outcome_end_ordinal"] = np.where(a["label_bin"] >= 0, a["outcome_end_ordinal"], 0).astype("int64")
    a["supervised_candidate"] = a["prediction_member"] & (a["label_bin"] >= 0)
    a["supervised_certified"] = np.zeros(n, bool)
    a["supervised"] = a["supervised_candidate"].copy()
    for prefix, key in (("seq", "seq"), ("market", "market_seq"), ("sector", "sector_seq")):
        ordinals = np.broadcast_to(np.arange(cutoff - t, cutoff)[None, :, None], a[key].shape).copy().astype("int64")
        a[prefix + "_source_ordinal"] = ordinals
        a[prefix + "_available_ordinal"] = ordinals.copy()
    for prefix, shape in (("event", (n, k)), ("edge", (n,))):
        a[prefix + "_source_ordinal"] = np.full(shape, cutoff - 1, "int64")
        a[prefix + "_available_ordinal"] = np.full(shape, cutoff - 1, "int64")
    for suffix in ("source", "available", "effective"):
        a["sector_mapping_" + suffix + "_ordinal"] = np.full(n, cutoff - 30, "int64")
    a["event_cache_key"] = np.empty((n, k), dtype="U64")
    a["event_body_sha256"] = np.empty((n, k), dtype="U64")
    for i in range(n):
        for j in range(k):
            a["event_cache_key"][i, j] = hashlib.sha256(f"synthetic-cache-{day}-{i}-{j}".encode()).hexdigest()
            a["event_body_sha256"][i, j] = hashlib.sha256(f"synthetic-body-{day}-{i}-{j}".encode()).hexdigest()
    return {key: a[key] for key in sorted(ROLES)}


def create_synthetic_manifest(directory):
    """Write private numerical test inputs only; no source/raw financial files."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    cache = {key: hashlib.sha256(("synthetic-" + key).encode()).hexdigest() for key in ("model_sha256", "tokenizer_sha256", "template_sha256")}
    cache.update(body_scope="body_only_excluding_system_wrapper_padding_generated_answer", records={})
    days = [("2019-01-07", "train"), ("2019-02-04", "train"), ("2019-03-04", "train"),
            ("2020-01-06", "selection"), ("2020-08-03", "selection"),
            ("2020-09-07", "calibration"), ("2020-12-07", "calibration"),
            ("2021-01-04", "test"), ("2021-01-05", "test"), ("2021-01-06", "test")]
    items, index = [], []
    sample = None
    for day, split in days:
        a = synthetic_batch(day, unknown=split == "test")
        sample = a
        target = root / (day + ".npz")
        np.savez_compressed(target, **a)
        items.append({"path": target.name, "sha256": sha256(target), "date": day, "split": split})
        for i in np.flatnonzero(a["prediction_member"]):
            row = {key: int(a[key][i]) for key in ("prediction_member", "supervised", "supervised_candidate", "supervised_certified", "label_bin", "outcome_end_ordinal", "observed_outcome_end_ordinal")}
            row.update(date=day, stock_code=str(a["stock_code"][i]), split=split, outcome_status=str(a["outcome_status"][i]), unknown_reason=str(a["unknown_reason"][i]))
            index.append(row)
        for i in range(len(a["stock_code"])):
            for k in range(a["event_mask"].shape[1]):
                cache["records"][str(a["event_cache_key"][i, k])] = {
                    "body_sha256": str(a["event_body_sha256"][i, k]), "stock_code": str(a["stock_code"][i]),
                    "vector_sha256": hashlib.sha256(a["events"][i, k].tobytes()).hexdigest(),
                    "predicted_fields_sha256": hashlib.sha256(a["predicted_fields"][i, k].tobytes()).hexdigest(),
                    "field_mask_sha256": hashlib.sha256(a["field_mask"][i, k].tobytes()).hexdigest(),
                    "available_ordinal": int(a["event_available_ordinal"][i, k])}
    with (root / "prediction_index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(index[0])); writer.writeheader(); writer.writerows(index)
    write_json(root / "frozen_event_cache.json", cache)
    manifest = {"schema": SCHEMA, "target_version": "actual-loss-four-bin-v1-2026-11-26",
                "task": "actual_five_market_session_loss_four_bins", "scope": "synthetic_cpu_only",
                "formal_training_allowed": False, "universe_certified": False, "validation_year": 2020, "fold": "synthetic-2021",
                "quality_sources": {"body_quality": "source_body_traceability", "evidence_quality": "literal_evidence_traceability"},
                "fields": [{"name": key, "dtype": str(sample[key].dtype), "role": ROLES[key]} for key in sample],
                "feature_order": {group: [{"name": name, "depends_on": dependency} for name, dependency in features] for group, features in {
                    "stock": [("stock_return", ["stock"]), ("past_volatility", ["stock"]), ("market_derived_control", ["stock", "market"])],
                    "market": [("raw_market_return", ["market"]), ("past_market_volatility", ["market"])],
                    "sector": [("historical_sector_return", ["sector"]), ("past_sector_volatility", ["sector"])],
                    "predicted": [("predicted_direction", ["event"]), ("predicted_cause", ["event"])]}.items()},
                "event_dim": 8, "batches": items,
                "prediction_index": {"path": "prediction_index.csv", "sha256": sha256(root / "prediction_index.csv")},
                "frozen_event_cache": {"path": "frozen_event_cache.json", "sha256": sha256(root / "frozen_event_cache.json")}}
    write_json(root / "manifest.json", manifest)
    return root / "manifest.json"


def predictions(model, items, contract, device="cpu", calibration=None):
    """Export every prediction-index member, including unknown future outcomes."""
    model.eval(); rows = []
    temperature = 1. if calibration is None else calibration["temperature"]
    with torch.inference_mode():
        for item in items:
            b, strings = contract.load(item, device, model.mode)
            out = model(b)
            raw = out["probability"].cpu().numpy()
            calibrated = temperature_scale(raw, temperature)
            cumulative = cumulative_probabilities(calibrated)
            for i in np.flatnonzero(b["prediction_member"].cpu().numpy()):
                known = int(b["label_bin"][i]) >= 0
                row = {"stock_code": strings["stock_code"][i], "date": item["date"], "fold": str(contract.manifest["fold"]),
                       "split": item["split"], "industry": strings["sector_key"][int(b["sector_index"][i])],
                       "prediction_member": 1, "supervised": int(b["supervised"][i]),
                       "supervised_candidate": int(b["supervised_candidate"][i]), "supervised_certified": int(b["supervised_certified"][i]),
                       "label_bin": int(b["label_bin"][i]) if known else "", "outcome_status": strings["outcome_status"][i],
                       "outcome_end_ordinal": int(b["outcome_end_ordinal"][i]),
                       "observed_outcome_end_ordinal": int(b["observed_outcome_end_ordinal"][i]), "unknown_reason": strings["unknown_reason"][i],
                       "market_context_available": int(b["market_mask"][int(b["market_index"][i])].any()),
                       "sector_context_available": int(b["sector_mask"][int(b["sector_index"][i])].any()),
                       "event_available": int(out["event_available"][i]), "graph_available": int(out["graph_available"][i]),
                       "short_history": int(not b["seq_mask"][i].any(-1).all()),
                       "degraded_auxiliary": int(not out["event_available"][i] or not out["graph_available"][i]),
                       "calibration_fitted": int(calibration is not None), "calibration_quality_verified": 0,
                       "temperature": float(temperature), "raw_age_bound_applies_after_temperature": int(temperature == 1. and out["raw_reliability_bound_applies"]),
                       "scope": contract.manifest["scope"], "universe_certified": 0,
                       "index_sha256": contract.identity["index_sha256"], "cache_sha256": contract.identity["cache_sha256"]}
                row.update({name: float(cumulative[name][i]) for name in PROBABILITY_COLUMNS})
                row.update({name: float(calibrated[i, j]) for j, name in enumerate(BIN_COLUMNS)})
                row.update({"raw_" + name: float(raw[i, j]) for j, name in enumerate(BIN_COLUMNS)})
                rows.append(row)
    return rows


def _rng_state():
    return {"python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state()}


def _restore_rng(state):
    random.setstate(state["python"]); np.random.set_state(state["numpy"]); torch.set_rng_state(state["torch"])


def save_checkpoint(path, model, optimizer, position, config, identity, training_state):
    torch.save({"checkpoint_schema": "actual-loss-checkpoint-v1", "model": model.state_dict(),
                "optimizer": optimizer.state_dict(), "rng": _rng_state(), "train_position": position,
                "config": config, "identity": identity, "training_state": training_state}, path)


def restore_checkpoint(path, model, optimizer, config, identity):
    # Only locally created, identity-verified research checkpoints are accepted.
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    if checkpoint.get("checkpoint_schema") != "actual-loss-checkpoint-v1" or checkpoint["config"] != config or checkpoint["identity"] != identity:
        raise ValueError("Checkpoint configuration/index/cache/input/code hash mismatch")
    model.load_state_dict(checkpoint["model"]); optimizer.load_state_dict(checkpoint["optimizer"])
    _restore_rng(checkpoint["rng"])
    return checkpoint["train_position"], checkpoint["training_state"]


def train_synthetic(contract, outdir, *, mode="full", seed=42, epochs=2, lr=.001, modality_dropout=.1,
                    resume=None, stop_after_steps=None, expert_aux=None):
    if contract.manifest["scope"] != "synthetic_cpu_only":
        raise ValueError("Real/candidate financial training is blocked; use --validate-only")
    if epochs < 1 or not np.isfinite(lr) or lr <= 0:
        raise ValueError("Invalid synthetic training settings")
    outdir = Path(outdir).resolve()
    repo = Path(__file__).resolve().parents[2]
    if outdir == repo or repo in outdir.parents:
        raise ValueError("All model outputs must be outside Git")
    outdir.mkdir(parents=True, exist_ok=True)
    # Validate every shard *before* optimizer construction and again on every load.
    for item in contract.items:
        contract.load(item, "cpu", mode)
    contract.assert_unchanged()
    config = {"mode": mode, "seed": seed, "epochs": epochs, "lr": lr, "modality_dropout": modality_dropout,
              "expert_aux": expert_aux or {}, "objective_role": "controlled_expert_auxiliary_sensitivity" if expert_aux else "primary_mixture_four_bin_NLL",
              "weight_decay": .0001, "device": "cpu", "selection_criterion": "four_bin_NLL",
              "torch": str(torch.__version__), "threads": 2}
    code = fingerprints([Path(__file__), Path(__file__).with_name("research_model.py"), Path(__file__).with_name("risk_batch_contract.py"),
                         Path(__file__).with_name("probability_calibration.py"), Path(__file__).with_name("evaluate_predictions.py")])
    identity = {**contract.identity, "code_hashes": code}
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.set_num_threads(2)
    sizes = {group: len(fields) for group, fields in contract.manifest["feature_order"].items()}
    model = DisclosureModel(features=sizes["stock"], event_dim=contract.manifest["event_dim"], market_features=sizes["market"],
                            sector_features=sizes["sector"], structured_dim=sizes["predicted"], mode=mode,
                            modality_dropout=modality_dropout, expert_aux=expert_aux)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=.0001)
    position = {"epoch": 0, "batch": 0, "step": 0}
    state = {"best_selection_NLL": None, "best_model": None, "stale": 0, "history": [], "current_losses": []}
    if resume:
        position, state = restore_checkpoint(resume, model, optimizer, config, identity)
    items = contract.groups["train"]
    while position["epoch"] < epochs and state["stale"] < 3:
        model.train()
        while position["batch"] < len(items):
            b, _ = contract.load(items[position["batch"]], "cpu", mode)
            if b["supervised"].any():
                optimizer.zero_grad(set_to_none=True)
                loss = model.loss(model(b), b["label_bin"], b["supervised"])
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite synthetic loss")
                loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.); optimizer.step()
                state["current_losses"].append(float(loss.detach()))
            position["batch"] += 1; position["step"] += 1
            save_checkpoint(outdir / "checkpoint.pt", model, optimizer, position, config, identity, state)
            if stop_after_steps is not None and position["step"] >= stop_after_steps:
                contract.assert_unchanged()
                return {"scope": "synthetic_cpu_only", "interrupted": True, "train_position": position}
        selection = predictions(model, contract.groups["selection"], contract)
        observed = [r for r in selection if r["supervised"]]
        if not observed:
            raise ValueError("No observable supervised selection outcomes")
        p = np.asarray([[r[name] for name in BIN_COLUMNS] for r in observed]); y = np.asarray([r["label_bin"] for r in observed])
        nll = float(-np.log(p[np.arange(len(y)), y]).mean())
        state["history"].append({"epoch": position["epoch"] + 1, "synthetic_train_NLL": float(np.mean(state["current_losses"])), "synthetic_selection_NLL": nll})
        if state["best_selection_NLL"] is None or nll < state["best_selection_NLL"]:
            state["best_selection_NLL"] = nll; state["best_model"] = copy.deepcopy(model.state_dict()); state["stale"] = 0
        else:
            state["stale"] += 1
        state["current_losses"] = []
        position["epoch"] += 1; position["batch"] = 0
        save_checkpoint(outdir / "checkpoint.pt", model, optimizer, position, config, identity, state)
    if state["best_model"] is None:
        raise ValueError("No selected synthetic checkpoint")
    model.load_state_dict(state["best_model"])
    calibration_rows = [r for r in predictions(model, contract.groups["calibration"], contract) if r["supervised"]]
    p = np.asarray([[r[name] for name in BIN_COLUMNS] for r in calibration_rows]); y = np.asarray([r["label_bin"] for r in calibration_rows])
    days = [r["date"] for r in calibration_rows]; ends = [r["observed_outcome_end_ordinal"] for r in calibration_rows]
    year, test_start = contract.manifest["validation_year"], min(i["date"] for i in contract.groups["test"])
    calibration = fit_temperature(p, y, days, ends, year, test_start)
    cumulative = cumulative_probabilities(temperature_scale(p, calibration["temperature"]))
    thresholds = {name: select_threshold(cumulative[name], target, days=days, outcome_end=ends, validation_year=year, test_start=test_start)
                  for name, target in zip(PROBABILITY_COLUMNS, ((y > 0), (y >= 2), (y == 3)))}
    rows = predictions(model, contract.groups["test"], contract, calibration=calibration)
    with (outdir / "test_predictions.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    write_json(outdir / "calibration.json", calibration); write_json(outdir / "thresholds.json", thresholds)
    report = {"scope": "synthetic_functional_CPU_ONLY_not_financial_scores", "formal_training_allowed": False,
              "universe_certified": False, "real_daily_assembler_implemented": False,
              "config": config, "identity": identity, "history": state["history"], "train_position": position,
              "prediction_universe_rows": len(rows), "supervised_test_rows": sum(r["supervised"] for r in rows),
              "calibration": calibration, "thresholds": thresholds,
              "synthetic_evaluation": evaluate_risk(pd.DataFrame(rows).replace({"label_bin": {"": np.nan}}), thresholds)}
    contract.assert_unchanged()
    write_json(outdir / "run_report.json", report)
    torch.save({"model": model.state_dict(), "config": config, "identity": identity, "calibration": calibration, "thresholds": thresholds}, outdir / "selected_model.pt")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--synthetic-smoke", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--mode", default="full", choices=sorted(DisclosureModel.MODES))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--lr", type=float, default=.001)
    parser.add_argument("--modality-dropout", type=float, default=.1)
    parser.add_argument("--stop-after-steps", type=int)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    resolved_output = args.output_dir.resolve()
    if resolved_output == repo or repo in resolved_output.parents:
        raise ValueError("All outputs must be outside Git")
    if args.resume and args.validate_only:
        raise ValueError("Validation-only does not resume model optimization")
    if args.resume:
        outdir = args.output_dir.resolve()
        if not outdir.is_dir():
            raise ValueError("Resume requires the existing private output directory")
    else:
        outdir = new_private_directory(args.output_dir)
    manifest = args.manifest
    if args.synthetic_smoke and manifest is None:
        manifest = outdir / "synthetic_inputs" / "manifest.json" if args.resume else create_synthetic_manifest(outdir / "synthetic_inputs")
    if manifest is None:
        raise ValueError("Need a strict manifest or --synthetic-smoke")
    contract = RiskBatchContract(manifest)
    if args.validate_only:
        for item in contract.items:
            contract.load(item, "cpu", args.mode)
        contract.assert_unchanged()
        report = {"schema": SCHEMA, "validation_passed": True, "scope": contract.manifest["scope"], "formal_training_allowed": False,
                  "identity": contract.identity, "batches": len(contract.items), "prediction_index_members": len(contract.index)}
        write_json(outdir / "contract_validation.json", report)
    else:
        if not args.synthetic_smoke:
            raise ValueError("Optimization requires explicit --synthetic-smoke; no formal financial training")
        report = train_synthetic(contract, outdir, mode=args.mode, seed=args.seed, epochs=args.epochs, lr=args.lr,
                                 modality_dropout=args.modality_dropout, resume=args.resume, stop_after_steps=args.stop_after_steps)
    print(json.dumps({k: v for k, v in report.items() if k not in {"synthetic_evaluation", "identity"}}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
