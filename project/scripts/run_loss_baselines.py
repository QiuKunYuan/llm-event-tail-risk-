"""SYNTHETIC-only four-bin Logit/LightGBM tabular engineering runner.

Uses the SAME RiskBatchContract/index/shard/cache provenance, not residual labels
or an unverified tabular NPZ. Explicit deterministic features expose all masked
past stock/market/sector values, frozen bodies, model-predicted fields, availability
and leave-target-out accepted-holdings peer summaries. This is a tabular input
transform, NOT a claim of identical GRU representations. Imputation, scaling and
optional frozen-body PCA fit TRAIN ONLY. At most eight configurations are chosen
on selection AP for >=5% loss; final temperature and alerts use calibration only.
Real/candidate financial optimization is rejected before preprocessing or models.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from research_model import peer_messages, weighted_mean
from risk_batch_contract import RiskBatchContract, SPLITS
from train_research_model import create_synthetic_manifest
from probability_calibration import BIN_COLUMNS, PROBABILITY_COLUMNS, cumulative_probabilities, fit_temperature, normalized_probabilities, select_threshold, temperature_scale
from evaluate_predictions import evaluate_risk
from validation_io import fingerprints, new_private_directory, write_json


def _key_hash(rows):
    return hashlib.sha256(json.dumps([(r["date"], r["stock_code"]) for r in rows], separators=(",", ":")).encode()).hexdigest()


def tabular_day(batch, strings, feature_order):
    """No labels, future endpoints or company/date identifiers enter X."""
    n = len(batch["seq"])
    columns, names, body_columns = [], [], []
    def append(values, observed, prefix, body=False):
        values, observed = np.asarray(values), np.asarray(observed, bool)
        if values.shape != observed.shape or len(values) != n:
            raise ValueError("Tabular value/mask shape mismatch")
        flat, valid = values.reshape(n, -1), observed.reshape(n, -1)
        for j in range(flat.shape[1]):
            if body:
                body_columns.append(len(columns))
            columns.append(np.where(valid[:, j], flat[:, j], np.nan)); names.append(f"{prefix}_value_{j}")
            columns.append(valid[:, j].astype(float)); names.append(f"{prefix}_observed_{j}")
    def array(key):
        return batch[key].cpu().numpy()
    for prefix, sequence, mask, group in (("stock", "seq", "seq_mask", "stock"), ("market", "market_seq", "market_mask", "market"),
                                          ("sector", "sector_seq", "sector_mask", "sector")):
        x, m = array(sequence), array(mask)
        if prefix != "stock":
            index = array(prefix + "_index"); x, m = x[index], m[index]
        for j, descriptor in enumerate(feature_order[group]):
            append(x[..., j], m[..., j], prefix + "_" + descriptor["name"])
    event_mask = array("event_mask").astype(bool)
    body_mask = event_mask & array("body_available").astype(bool)
    append(array("events"), np.broadcast_to(body_mask[..., None], array("events").shape), "frozen_body", body=True)
    fields = array("predicted_fields")
    field_mask = array("field_mask").astype(bool) & event_mask[..., None] & array("event_json_valid").astype(bool)[..., None]
    for j, descriptor in enumerate(feature_order["predicted"]):
        append(fields[..., j], field_mask[..., j], "predicted_" + descriptor["name"])
    for key in ("event_age", "body_quality", "evidence_quality"):
        append(array(key), event_mask, key)
    for key in ("event_mask", "body_available", "event_json_valid"):
        append(array(key).astype(float), np.ones_like(event_mask), key)
    own = np.column_stack(columns)
    # Explicit known price+event channels, availability-aware aggregation. No neighbor labels.
    filled = np.nan_to_num(own, nan=0.)
    observed = np.isfinite(own).astype(float)
    ci, ii = batch["edges"]
    weights = batch["edge_share"].double()
    messages = torch.as_tensor(np.concatenate((filled, observed), -1), dtype=torch.float64)[ci]
    peers, peer_weights = peer_messages(messages, ci, ii, weights, n)
    pooled = weighted_mean(peers, ci, peer_weights, n).numpy()
    d = own.shape[1]
    values, available = pooled[:, :d], pooled[:, d:] > 0
    values = values / np.maximum(pooled[:, d:], np.finfo(float).tiny)
    old_names, old_body = names.copy(), set(body_columns)
    for j in range(d):
        if j in old_body:
            body_columns.append(len(columns))
        columns.append(np.where(available[:, j], values[:, j], np.nan)); names.append("peer_" + old_names[j])
        columns.append(available[:, j].astype(float)); names.append("peer_available_" + old_names[j])
    degree = np.bincount(ci.numpy(), minlength=n)
    peer_degree = np.bincount(ci[peer_weights > 0].numpy(), minlength=n)
    edge_sum = np.bincount(ci.numpy(), weights=weights.numpy(), minlength=n)
    age_sum = np.bincount(ci.numpy(), weights=weights.numpy() * array("edge_age"), minlength=n)
    mean_age = np.where(edge_sum > 0, age_sum / np.maximum(edge_sum, np.finfo(float).tiny), np.nan)
    for name, values in (("accepted_edge_degree", degree), ("available_peer_degree", peer_degree),
                         ("accepted_edge_share_sum", edge_sum), ("accepted_weighted_edge_age", mean_age),
                         ("graph_age", array("graph_age")), ("graph_quality", array("graph_quality"))):
        columns.append(np.asarray(values, float)); names.append(name)
    return np.column_stack(columns), names, body_columns


def tabular_view(contract):
    rows, arrays, names, body_columns = [], [], None, None
    for item in contract.items:
        b, strings = contract.load(item, "cpu")
        x, order, body = tabular_day(b, strings, contract.manifest["feature_order"])
        if names is None:
            names, body_columns = order, body
        elif order != names or body != body_columns:
            raise ValueError("Tabular feature/field order changed across daily shards")
        selected = np.flatnonzero(b["prediction_member"].numpy())
        arrays.append(x[selected])
        for i in selected:
            rows.append({"stock_code": str(strings["stock_code"][i]), "date": item["date"], "split": item["split"],
                         "fold": str(contract.manifest["fold"]), "industry": str(strings["sector_key"][int(b["sector_index"][i])]),
                         "prediction_member": 1, "label_bin": int(b["label_bin"][i]), "supervised": bool(b["supervised"][i]),
                         "supervised_candidate": int(b["supervised_candidate"][i]), "supervised_certified": int(b["supervised_certified"][i]),
                         "outcome_status": str(strings["outcome_status"][i]), "unknown_reason": str(strings["unknown_reason"][i]),
                         "outcome_end_ordinal": int(b["outcome_end_ordinal"][i]), "observed_outcome_end_ordinal": int(b["observed_outcome_end_ordinal"][i])})
    contract.assert_unchanged()
    return np.concatenate(arrays), rows, names, body_columns


class TrainOnlyTransform:
    """Frozen impute/body-PCA/scale state; transform never learns from held-out data."""
    def __init__(self, names, body_columns, pca_components=0):
        self.names = tuple(names); self.body_columns = tuple(body_columns); self.requested_pca = int(pca_components)
        if self.requested_pca < 0 or len(set(self.names)) != len(self.names):
            raise ValueError("Invalid PCA rank or duplicate tabular feature names")
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        self.scaler = StandardScaler(); self.pca = None

    def fit(self, x, rows):
        if len(x) != len(rows) or not len(x) or any(r["split"] != "train" or not r["supervised"] for r in rows):
            raise ValueError("Imputation/scaling/PCA must fit supervised TRAIN rows only")
        self.fit_rows = len(rows); self.fit_keys_sha256 = _key_hash(rows)
        self.fit_date_start = min(r["date"] for r in rows); self.fit_date_end = max(r["date"] for r in rows)
        self.nonbody_columns = tuple(i for i in range(len(self.names)) if i not in set(self.body_columns))
        values = self.imputer.fit_transform(x)
        if self.requested_pca and self.body_columns:
            rank = min(self.requested_pca, len(values), len(self.body_columns))
            self.pca = PCA(n_components=rank, svd_solver="full").fit(values[:, self.body_columns])
        projected = self._project(values)
        self.scaler.fit(projected)
        self.output_names = [f"f{i:04d}" for i in range(projected.shape[1])]
        return self

    def _project(self, values):
        if self.pca is None:
            return values
        return np.concatenate((values[:, self.nonbody_columns], self.pca.transform(values[:, self.body_columns])), -1)

    def transform(self, x, names=None):
        if x.ndim != 2 or x.shape[1] != len(self.names) or (names is not None and tuple(names) != self.names):
            raise ValueError("Baseline input feature order/shape mismatch")
        values = self.scaler.transform(self._project(self.imputer.transform(x)))
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite transformed baseline features")
        return pd.DataFrame(values, columns=self.output_names)

    def serialization_state(self):
        # Store library components, not a __main__ custom class in CLI artifacts.
        return {"imputer": self.imputer, "scaler": self.scaler, "body_PCA": self.pca,
                "body_columns": self.body_columns, "nonbody_columns": self.nonbody_columns,
                "output_names": self.output_names, "metadata": self.metadata()}

    def metadata(self):
        return {"fit_role": "train", "fit_rows": self.fit_rows, "fit_keys_sha256": self.fit_keys_sha256,
                "fit_date_start": self.fit_date_start, "fit_date_end": self.fit_date_end,
                "raw_feature_order": list(self.names), "body_columns": list(self.body_columns),
                "PCA_requested": self.requested_pca, "PCA_fitted_components": self.pca.n_components_ if self.pca is not None else 0,
                "imputation": "train_median_empty_columns_explicit_zero_with_observation_masks", "scaling": "train_only_standard",
                "feature_transform": "explicit_masked_past_values_frozen_body_predicted_fields_leave_target_out_peers"}


def search_configurations(families):
    if not families or len(set(families)) != len(families) or set(families) - {"logit", "lightgbm"}:
        raise ValueError("Supported synthetic baseline families are logit/lightgbm")
    configs = []
    for family in families:
        for setting in ((.1, 1.) if family == "logit" else (3, 7)):
            for rank in (0, 4):
                configs.append({"family": family, "body_PCA": rank, **({"C": setting} if family == "logit" else {"num_leaves": setting})})
    return configs


def _classifier(config, seed):
    if config["family"] == "logit":
        return LogisticRegression(C=config["C"], solver="lbfgs", max_iter=500, random_state=seed)
    try:
        from lightgbm import LGBMClassifier
    except ImportError as exc:
        raise ValueError("LightGBM is unavailable; request logit only. No installation is performed") from exc
    return LGBMClassifier(objective="multiclass", num_class=4, num_leaves=config["num_leaves"],
                          n_estimators=32, min_child_samples=2, learning_rate=.05, reg_lambda=1.,
                          random_state=seed, n_jobs=2, device_type="cpu", deterministic=True, force_col_wise=True, verbosity=-1)


def classifier_probabilities(model, x):
    if not np.array_equal(model.classes_, np.arange(4)):
        raise ValueError("Four-bin classifier training must contain all classes 0..3")
    return normalized_probabilities(model.predict_proba(x))


def run_synthetic_baselines(contract, output_dir, *, families=("logit", "lightgbm"), configurations=None, seed=42):
    # Fail before feature extraction, preprocessing, classifier construction or optimization.
    if contract.manifest.get("scope") != "synthetic_cpu_only":
        raise ValueError("Real/unverified candidate baseline optimization is blocked")
    configs = search_configurations(families) if configurations is None else list(configurations)
    if not configs or len(configs) > 8 or any(c.get("family") not in families or c.get("body_PCA", -1) not in (0, 4) for c in configs):
        raise ValueError("Predeclared selection search requires at most eight supported configurations")
    for config in configs:
        required = {"family", "body_PCA", "C" if config["family"] == "logit" else "num_leaves"}
        if set(config) != required or (config["family"] == "logit" and config["C"] <= 0):
            raise ValueError("Invalid explicit baseline configuration")
    output = new_private_directory(output_dir)
    x, rows, names, body = tabular_view(contract)
    roles = np.asarray([r["split"] for r in rows]); y = np.asarray([r["label_bin"] for r in rows])
    supervised = np.asarray([r["supervised"] for r in rows], bool)
    masks = {role: (roles == role) & supervised for role in SPLITS}
    if any(not masks[role].any() for role in SPLITS[:-1]) or not (roles == "test").any() or set(y[masks["train"]]) != {0, 1, 2, 3}:
        raise ValueError("Synthetic tabular training needs four train bins, observable train/selection/calibration and a prediction test role")
    if not (y[masks["selection"]] >= 2).any():
        raise ValueError("Selection AP loss_5pct is undefined without positive selection labels")
    train_rows = [r for r, selected in zip(rows, masks["train"]) if selected]
    search, chosen = [], {}
    with threadpool_limits(limits=2):
        for number, config in enumerate(configs):
            transform = TrainOnlyTransform(names, body, config["body_PCA"]).fit(x[masks["train"]], train_rows)
            model = _classifier(config, seed)
            model.fit(transform.transform(x[masks["train"]], names), y[masks["train"]])
            probability = classifier_probabilities(model, transform.transform(x[masks["selection"]], names))
            ap = float(average_precision_score(y[masks["selection"]] >= 2, probability[:, 2:].sum(1)))
            search.append({"config_id": number, "config": config, "selection_AP_loss_5pct": ap,
                           "selection_role": "selection", "preprocessing_fit_keys_sha256": transform.fit_keys_sha256})
            family = config["family"]
            if family not in chosen or ap > chosen[family]["AP"]:
                chosen[family] = {"model": model, "transform": transform, "config": config, "config_id": number, "AP": ap}
    test_mask = roles == "test"  # All prediction members, before future-label filtering.
    prediction_rows = [{**r, "label_bin": r["label_bin"] if r["label_bin"] >= 0 else "",
                        "scope": "synthetic_cpu_only", "calibration_quality_verified": 0,
                        "index_sha256": contract.identity["index_sha256"], "cache_sha256": contract.identity["cache_sha256"]}
                       for r, selected in zip(rows, test_mask) if selected]
    reports = {}
    cal_rows = [r for r, selected in zip(rows, masks["calibration"]) if selected]
    days = [r["date"] for r in cal_rows]; ends = [r["observed_outcome_end_ordinal"] for r in cal_rows]
    test_start = min(r["date"] for r in rows if r["split"] == "test")
    code_identity = fingerprints([Path(__file__), Path(__file__).with_name("risk_batch_contract.py"), Path(__file__).with_name("research_model.py"),
        Path(__file__).with_name("probability_calibration.py"), Path(__file__).with_name("evaluate_predictions.py"), Path(__file__).with_name("validation_io.py")])
    for family, fit in chosen.items():
        transform, model = fit["transform"], fit["model"]
        raw_cal = classifier_probabilities(model, transform.transform(x[masks["calibration"]], names))
        cal = fit_temperature(raw_cal, y[masks["calibration"]], days, ends, contract.manifest["validation_year"], test_start)
        cal["raw_age_bound_applies_after_temperature"] = False
        cal["age_bound_not_a_property_of_this_baseline"] = True
        cumulative = cumulative_probabilities(temperature_scale(raw_cal, cal["temperature"]))
        cal_y = y[masks["calibration"]]
        thresholds = {name: select_threshold(cumulative[name], target, days=days, outcome_end=ends,
                      validation_year=contract.manifest["validation_year"], test_start=test_start)
                      for name, target in zip(PROBABILITY_COLUMNS, (cal_y > 0, cal_y >= 2, cal_y == 3))}
        raw_test = classifier_probabilities(model, transform.transform(x[test_mask], names))
        probability = temperature_scale(raw_test, cal["temperature"]); cumulative = cumulative_probabilities(probability)
        view = []
        for i, row in enumerate(prediction_rows):
            row.update({family + "_" + name: float(probability[i, j]) for j, name in enumerate(BIN_COLUMNS)})
            row.update({family + "_" + name: float(cumulative[name][i]) for name in PROBABILITY_COLUMNS})
            row[family + "_calibration_fitted"] = 1; row[family + "_temperature"] = cal["temperature"]
            canonical = {**row, **{name: float(probability[i, j]) for j, name in enumerate(BIN_COLUMNS)}}
            canonical["label_bin"] = np.nan if row["label_bin"] == "" else row["label_bin"]
            view.append(canonical)
        report = {"selected_config_id": fit["config_id"], "selected_config": fit["config"], "selection_AP_loss_5pct": fit["AP"],
                  "preprocessing": transform.metadata(), "calibration": cal, "thresholds": thresholds,
                  "synthetic_evaluation": evaluate_risk(pd.DataFrame(view), thresholds)}
        reports[family] = report
        joblib.dump({"model": model, "preprocessor": transform.serialization_state(), "config": fit["config"], "calibration": cal, "thresholds": thresholds,
                     "scope": "synthetic_cpu_only", "identity": contract.identity, "code_hashes": code_identity}, output / (family + "_synthetic_model.joblib"))
    contract.assert_unchanged()
    with (output / "test_predictions.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(prediction_rows[0])); writer.writeheader(); writer.writerows(prediction_rows)
    report = {"scope": "synthetic_functional_CPU_baselines_ONLY_not_financial_results", "formal_training_allowed": False,
              "real_candidate_optimization_allowed": False, "legacy_residual_AP_reused": False, "seed": seed,
              "search_configs": search, "search_budget": len(configs), "selection_criterion": "selection_AP_loss_5pct",
              "prediction_universe_test_rows": len(prediction_rows), "supervised_test_rows": int(masks["test"].sum()),
              "raw_feature_order": names, "identity": contract.identity, "code_hashes": code_identity, "families": reports}
    write_json(output / "synthetic_baseline_report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--synthetic-smoke", action="store_true")
    parser.add_argument("--families", nargs="+", choices=["logit", "lightgbm"], default=["logit", "lightgbm"])
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not args.synthetic_smoke:
        raise ValueError("Explicit --synthetic-smoke required; real financial baseline training is blocked")
    manifest = args.manifest
    if manifest is None:
        # Inputs and outputs remain private; fail before creating anything in Git.
        private = new_private_directory(args.output_dir)
        manifest = create_synthetic_manifest(private / "synthetic_inputs")
        output = private / "baselines"
    else:
        output = args.output_dir
    report = run_synthetic_baselines(RiskBatchContract(manifest), output, families=args.families, seed=args.seed)
    print(json.dumps({k: v for k, v in report.items() if k not in {"families", "raw_feature_order", "identity", "code_hashes"}}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
