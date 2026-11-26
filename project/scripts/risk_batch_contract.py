"""Fail-closed interface for future point-in-time daily risk assemblers.

This verifies a supplied panel/index/cache; it does not construct or certify real
financial data. Manifest v1 declares exact NPZ field order, dtype and role, named
feature order/dependencies, SHA256 for every shard/index/cache, and four split
roles train/selection/calibration/test. Each observed feature has source and
availability ordinals. seq/mask [N,T,F], market_seq/mask [1,T,Fm] and market_index
[N]=0, sector_seq/mask [C,T,Fi], unique sector_key [C] and sector_index [N]. The
sector mapping also binds source/available/effective dates, never latest industry.
Events use frozen body [N,K,D], predicted fields/mask [N,K,P], separate body/JSON
availability and traceability qualities; accepted-complete edges are [2,E].

Prediction membership is independent of future outcomes. label_bin=-1 is unknown;
expected outcome_end may exist then, but observed_outcome_end must be zero. Narrow
source views may use loss_bin and nullable ordinals; the adapter normalizes these,
retains the full index hash, and excludes only purged/out_of_range roles from shard
membership. Candidate manifests separately bind source_target_version_id (source
provenance SHA256), source_artifacts and model target_version (protocol string).
See create_synthetic_manifest in train_research_model for a complete private demo.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from validation_io import csv_rows, fingerprints, sha256, unchanged

SCHEMA = "actual-loss-risk-batch-v1"
SPLITS = ("train", "selection", "calibration", "test")

KEY_FIELDS = {"stock_code", "sector_key", "prediction_member", "outcome_status", "unknown_reason"}
LABEL_FIELDS = {"label_bin", "supervised", "supervised_candidate", "supervised_certified", "outcome_end_ordinal", "observed_outcome_end_ordinal"}
MASK_FIELDS = {"seq_mask", "market_mask", "sector_mask", "event_mask", "body_available",
               "event_json_valid", "field_mask", "edge_accepted_complete"}
PROVENANCE_FIELDS = {"event_body_sha256", "event_cache_key", "sector_mapping_source_ordinal",
                     "sector_mapping_available_ordinal", "sector_mapping_effective_ordinal"}
for _prefix in ("seq", "market", "sector", "event", "edge"):
    PROVENANCE_FIELDS.update({_prefix + "_source_ordinal", _prefix + "_available_ordinal"})
FEATURE_FIELDS = {"seq", "market_seq", "sector_seq", "market_index", "sector_index", "events",
                  "predicted_fields", "event_age", "body_quality", "evidence_quality", "edges",
                  "edge_age", "edge_share", "graph_age", "graph_quality"}
REQUIRED = KEY_FIELDS | LABEL_FIELDS | MASK_FIELDS | PROVENANCE_FIELDS | FEATURE_FIELDS
ROLES = {name: role for names, role in ((KEY_FIELDS, "key"), (LABEL_FIELDS, "outcome"),
                                        (MASK_FIELDS, "mask"), (PROVENANCE_FIELDS, "provenance"),
                                        (FEATURE_FIELDS, "feature")) for name in names}


def _boolean(value, name):
    if value.dtype != np.dtype("bool"):
        raise ValueError(name + " must have boolean dtype")


def _integer(value, name):
    if value.dtype.kind not in "iu":
        raise ValueError(name + " must have integer dtype")


def _dates(source, available, active, cutoff, name):
    if source.shape != active.shape or available.shape != active.shape:
        raise ValueError("Per-record provenance shape mismatch: " + name)
    _integer(source, name + " source date"); _integer(available, name + " available date")
    if ((source[active] <= 0) | (source[active] > available[active]) | (available[active] > cutoff)).any():
        raise ValueError("Future/missing per-record source or availability date: " + name)


def _sha_string(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class RiskBatchContract:
    """Validate exact artifacts and index records before any CPU risk optimization."""
    def __init__(self, manifest_path):
        self.path = Path(manifest_path).resolve()
        self.root = self.path.parent
        self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
        m = self.manifest
        if (m.get("schema") != SCHEMA or m.get("task") != "actual_five_market_session_loss_four_bins" or
                m.get("target_version") != "actual-loss-four-bin-v1-2026-11-26"):
            raise ValueError("Wrong risk schema or outcome task")
        if m.get("scope") not in {"synthetic_cpu_only", "candidate_panel_not_certified"} or m.get("formal_training_allowed") is not False or m.get("universe_certified") is not False:
            raise ValueError("This trainer is not a formal financial-training certification entry")
        if m.get("quality_sources") != {"body_quality": "source_body_traceability", "evidence_quality": "literal_evidence_traceability"}:
            raise ValueError("Quality cannot be self-reported confidence or test-gold semantic correctness")
        self.fields = m["fields"]
        names = [f["name"] for f in self.fields]
        if len(names) != len(set(names)) or set(names) != REQUIRED:
            raise ValueError("Unknown/missing/duplicate schema fields")
        string_fields = {"stock_code", "sector_key", "outcome_status", "unknown_reason", "event_body_sha256", "event_cache_key"}
        boolean_fields = MASK_FIELDS | {"prediction_member", "supervised", "supervised_candidate", "supervised_certified"}
        integer_fields = (LABEL_FIELDS | PROVENANCE_FIELDS | {"market_index", "sector_index", "edges"}) - string_fields - boolean_fields
        for field in self.fields:
            name = field["name"]
            if field.get("role") != ROLES[name] or not field.get("dtype"):
                raise ValueError("Field role/dtype mismatch: " + name)
            dtype = np.dtype(field["dtype"])
            allowed = "US" if name in string_fields else "b" if name in boolean_fields else "i" if name in integer_fields else "f"
            if dtype.kind not in allowed or (dtype.kind == "i" and dtype.itemsize != 8) or (dtype.kind == "f" and dtype.itemsize not in (4, 8)):
                raise ValueError("Unsupported schema dtype for " + name)
        for group in ("stock", "market", "sector", "predicted"):
            fields = m["feature_order"][group]
            if len({f["name"] for f in fields}) != len(fields):
                raise ValueError("Duplicate named feature order")
            for field in fields:
                if set(field) != {"name", "depends_on"} or not set(field["depends_on"]) <= {"stock", "market", "sector", "event"}:
                    raise ValueError("Feature dependencies must be declared for context ablations")
                if any(x in field["name"].lower() for x in ("future", "label", "outcome", "confidence", "semantic_quality")):
                    raise ValueError("Outcome/self-confidence is not a risk input feature")
        self.items = sorted(m["batches"], key=lambda item: (item["date"], item["split"]))
        if not self.items or any(item["split"] not in SPLITS for item in self.items):
            raise ValueError("Invalid split role")
        if len({item["date"] for item in self.items}) != len(self.items):
            raise ValueError("Daily batches must be unique across roles")
        self.groups = {role: [item for item in self.items if item["split"] == role] for role in SPLITS}
        self.validation_only = m.get("purpose") == "candidate_input_validation_only"
        selection = m.get("explicit_selection")
        if self.validation_only:
            if m["scope"] != "candidate_panel_not_certified" or not selection or set(selection) != {"dates", "stock_codes"}:
                raise ValueError("Limited real batches need an explicit validation-only date/stock selection")
            if any(not values or values != sorted(set(values)) for values in selection.values()):
                raise ValueError("Explicit selection must be nonempty, sorted and unique")
            if {i["date"] for i in self.items} != set(selection["dates"]):
                raise ValueError("Batch dates differ from the explicit validation selection")
        elif selection is not None or any(not group for group in self.groups.values()):
            raise ValueError("Training manifests need full chronological train/selection/calibration/test batches")
        year = int(m["validation_year"])
        for item in self.groups["selection"] + self.groups["calibration"]:
            d = date.fromisoformat(item["date"])
            if d.year != year or (item["split"] == "selection") != (d.month <= 8):
                raise ValueError("Jan-Aug selection and Sep-Dec calibration required in prior validation year")
        if self.groups["test"] and min(date.fromisoformat(i["date"]).year for i in self.groups["test"]) != year + 1:
            raise ValueError("Test must follow the prior validation year")
        self.boundaries = {}
        if self.validation_only:
            for role in SPLITS[:-1]:
                boundary = m["split_boundaries"][role]
                self.boundaries[role] = date.fromisoformat(boundary).toordinal()
            if list(self.boundaries.values()) != sorted(set(self.boundaries.values())):
                raise ValueError("Invalid source-fold split boundaries")
        else:
            for role, next_role in zip(SPLITS, SPLITS[1:]):
                boundary = min(i["date"] for i in self.groups[next_role])
                if max(i["date"] for i in self.groups[role]) >= boundary:
                    raise ValueError("Overlapping chronological split roles")
                self.boundaries[role] = date.fromisoformat(boundary).toordinal()
        inputs = [self.path]
        if m["scope"] == "candidate_panel_not_certified" and (not m.get("source_artifacts") or not _sha_string(m.get("source_target_version_id"))):
            raise ValueError("Candidate view must bind original panel/index/source artifacts and source target digest")
        for artifact in [m["prediction_index"], m["frozen_event_cache"], *m.get("source_artifacts", []), *self.items]:
            p = self._artifact(artifact)
            inputs.append(p)
        self.input_hashes = fingerprints(inputs)
        self.cache = json.loads(self._artifact(m["frozen_event_cache"]).read_text(encoding="utf-8"))
        self.cache_unavailable = self.cache.get("status") == "unavailable_no_frozen_model"
        if self.cache_unavailable:
            if not self.validation_only or self.cache.get("records") != {} or any(self.cache.get(k) is not None for k in ("model_sha256", "tokenizer_sha256", "template_sha256", "body_scope")):
                raise ValueError("Unavailable event cache may only be explicit empty validation-only input")
        else:
            if not all(self.cache.get(k) for k in ("model_sha256", "tokenizer_sha256", "template_sha256", "body_scope")) or not isinstance(self.cache.get("records"), dict):
                raise ValueError("Frozen cache must bind model/tokenizer/template/body scope and source body")
            if any(not _sha_string(self.cache[k]) for k in ("model_sha256", "tokenizer_sha256", "template_sha256")):
                raise ValueError("Invalid frozen model identity")
            if self.cache["body_scope"] != "body_only_excluding_system_wrapper_padding_generated_answer":
                raise ValueError("Frozen body pooling scope is not audited")
        self.index = {}
        seen, previous, first_roles = set(), None, {}
        selected_days = set(selection["dates"]) if self.validation_only else None
        selected_codes = set(selection["stock_codes"]) if self.validation_only else None
        for record in csv_rows(self._artifact(m["prediction_index"])):
            key = (record["date"], record["stock_code"])
            if self.validation_only:
                ordered = (record["stock_code"], record["date"])
                if previous is not None and ordered <= previous:
                    raise ValueError("Limited validation source index must be sorted and unique stock/date")
                previous = ordered
            else:
                if key in seen:
                    raise ValueError("Duplicate prediction-index member")
                seen.add(key)
            if record["split"] not in {*SPLITS, "purged", "out_of_range"}:
                raise ValueError("Invalid canonical index role")
            if m["scope"] == "candidate_panel_not_certified" and record.get("target_version_id") != m["source_target_version_id"]:
                raise ValueError("Index source target provenance digest mismatch")
            if record["split"] not in SPLITS:
                continue
            first_roles[record["split"]] = min(record["date"], first_roles.get(record["split"], record["date"]))
            if self.validation_only and (key[0] not in selected_days or key[1] not in selected_codes):
                continue
            if record["prediction_member"] != "1":
                raise ValueError("Index must preserve prediction universe independently of outcomes")
            record["label_bin"] = record.get("label_bin", record.get("loss_bin", "")) or "-1"
            for name in ("outcome_end_ordinal", "observed_outcome_end_ordinal"):
                record[name] = record.get(name, "") or "0"
            record["supervised"] = record.get("supervised", record["supervised_candidate"] if m["scope"] == "synthetic_cpu_only" else record["supervised_certified"])
            self.index[key] = record
        if self.validation_only:
            for role, next_role in zip(SPLITS, SPLITS[1:]):
                if first_roles.get(next_role) != m["split_boundaries"][role]:
                    raise ValueError("Declared subset boundaries differ from full source index roles")
        if set(d for d, _ in self.index) != {i["date"] for i in self.items}:
            raise ValueError("Active canonical index dates and batch dates differ")
        self.identity = {"manifest_sha256": self.input_hashes[str(self.path)],
                         "index_sha256": m["prediction_index"]["sha256"],
                         "cache_sha256": m["frozen_event_cache"]["sha256"],
                         "input_hashes": self.input_hashes, "schema": SCHEMA}

    def _artifact(self, artifact):
        path = (self.root / artifact["path"]).resolve()
        if not _sha_string(artifact.get("sha256")) or sha256(path) != artifact["sha256"]:
            raise ValueError("Input SHA256 mismatch: " + str(path))
        return path

    def assert_unchanged(self):
        unchanged(self.input_hashes)

    def load(self, item, device="cpu", mode="full"):
        path = self._artifact(item)
        with np.load(path, allow_pickle=False) as shard:
            if shard.files != [f["name"] for f in self.fields]:
                raise ValueError("Shard schema/field order mismatch")
            arrays = {key: shard[key].copy() for key in shard.files}
        for field in self.fields:
            if str(arrays[field["name"]].dtype) != field["dtype"]:
                raise ValueError("Shard dtype mismatch: " + field["name"])
        self.validate(arrays, item)
        # Close indirect market/sector leakage in stock and other feature streams.
        removed = "market" if mode == "no_market" else "sector" if mode == "no_sector" else None
        if removed:
            for group, mask in (("stock", "seq_mask"), ("market", "market_mask"), ("sector", "sector_mask"), ("predicted", "field_mask")):
                for j, feature in enumerate(self.manifest["feature_order"][group]):
                    if removed in feature["depends_on"]:
                        arrays[mask][..., j] = False
        tensors = {key: torch.as_tensor(value, device=device).to(
            dtype=torch.float32 if value.dtype.kind == "f" else torch.long if value.dtype.kind in "iu" else torch.bool)
            for key, value in arrays.items() if value.dtype.kind not in "US"}
        return tensors, {key: value for key, value in arrays.items() if value.dtype.kind in "US"}

    def validate(self, a, item):
        n = len(a["stock_code"])
        cutoff = date.fromisoformat(item["date"]).toordinal()
        if n < 1 or a["stock_code"].ndim != 1 or len(set(a["stock_code"])) != n:
            raise ValueError("Duplicate/empty stock nodes")
        for key in MASK_FIELDS | {"prediction_member", "supervised", "supervised_candidate", "supervised_certified"}:
            _boolean(a[key], key)
        for key in ("label_bin", "outcome_end_ordinal", "observed_outcome_end_ordinal", "market_index", "sector_index", "edges"):
            _integer(a[key], key)
        for key in ("prediction_member", "supervised", "supervised_candidate", "supervised_certified", "label_bin", "outcome_status", "unknown_reason", "outcome_end_ordinal", "observed_outcome_end_ordinal", "market_index", "sector_index", "graph_age", "graph_quality"):
            if a[key].shape != (n,):
                raise ValueError("Invalid node vector " + key)
        for prefix, sequence, mask, group in (("seq", "seq", "seq_mask", "stock"), ("market", "market_seq", "market_mask", "market"), ("sector", "sector_seq", "sector_mask", "sector")):
            value, observed = a[sequence], a[mask]
            if value.ndim != 3 or value.shape != observed.shape or value.shape[-1] != len(self.manifest["feature_order"][group]):
                raise ValueError("Temporal feature order/shape mismatch")
            if not np.isfinite(value[observed]).all():
                raise ValueError("Nonfinite observed temporal features")
            _dates(a[prefix + "_source_ordinal"], a[prefix + "_available_ordinal"], observed, cutoff, prefix)
        if len(a["seq"]) != n or len(a["market_seq"]) != 1 or (a["market_index"] != 0).any():
            raise ValueError("Market must encode once per date using mapping")
        categories = a["sector_key"]
        if categories.ndim != 1 or len(set(categories)) != len(categories) or len(categories) != len(a["sector_seq"]):
            raise ValueError("Sector must encode once per historical category/date")
        if ((a["sector_index"] < 0) | (a["sector_index"] >= len(categories))).any():
            raise ValueError("Sector mapping out of bounds")
        effective = a["sector_mapping_effective_ordinal"]
        _integer(effective, "sector effective date")
        sector_unavailable = self.manifest.get("sector_mapping_status") == "unavailable_no_certified_history"
        if sector_unavailable:
            if (not self.validation_only or list(categories) != ["UNKNOWN"] or a["sector_mask"].any() or
                    (a["sector_index"] != 0).any() or effective.shape != (n,) or (effective != 0).any() or
                    a["sector_mapping_source_ordinal"].shape != (n,) or a["sector_mapping_available_ordinal"].shape != (n,) or
                    a["sector_mapping_source_ordinal"].any() or a["sector_mapping_available_ordinal"].any()):
                raise ValueError("Unavailable sector must be explicit UNKNOWN, fully masked, with no invented mapping dates")
        else:
            _dates(a["sector_mapping_source_ordinal"], a["sector_mapping_available_ordinal"], np.ones(n, bool), cutoff, "historical sector mapping")
            if effective.shape != (n,) or ((effective <= 0) | (effective > cutoff)).any():
                raise ValueError("Future/missing industry effective date")
        em = a["event_mask"]
        if em.ndim != 2 or len(em) != n or a["events"].shape != (*em.shape, self.manifest["event_dim"]):
            raise ValueError("Event dimension mismatch")
        for key in ("body_available", "event_json_valid", "event_age", "body_quality", "evidence_quality", "event_body_sha256", "event_cache_key"):
            if a[key].shape != em.shape:
                raise ValueError("Event metadata shape mismatch")
        _dates(a["event_source_ordinal"], a["event_available_ordinal"], em, cutoff, "event")
        if self.cache_unavailable and (em.any() or a["body_available"].any() or a["event_json_valid"].any() or a["field_mask"].any()):
            raise ValueError("No event features may be supplied without a real frozen cache")
        fields = a["predicted_fields"]
        if fields.shape != (*em.shape, len(self.manifest["feature_order"]["predicted"])) or fields.shape != a["field_mask"].shape:
            raise ValueError("Predicted field order mismatch")
        if (a["field_mask"] & ~(a["event_json_valid"] & em)[..., None]).any():
            raise ValueError("Missing event/invalid JSON cannot supply predicted structured fields")
        body_active = em & a["body_available"]
        if not np.isfinite(a["events"][body_active]).all() or not np.isfinite(fields[a["field_mask"]]).all():
            raise ValueError("Nonfinite available frozen event representation")
        feature_active = em & (a["body_available"] | a["field_mask"].any(-1))
        for i, k in zip(*np.where(feature_active)):
            key = str(a["event_cache_key"][i, k])
            record = self.cache["records"].get(key)
            if not record or not _sha_string(str(a["event_body_sha256"][i, k])) or record.get("body_sha256") != str(a["event_body_sha256"][i, k]):
                raise ValueError("Frozen cache/body binding mismatch")
            if body_active[i, k] and record.get("vector_sha256") != hashlib.sha256(a["events"][i, k].tobytes()).hexdigest():
                raise ValueError("Frozen cache/body vector binding mismatch")
            if a["field_mask"][i, k].any() and (
                    record.get("predicted_fields_sha256") != hashlib.sha256(fields[i, k].tobytes()).hexdigest() or
                    record.get("field_mask_sha256") != hashlib.sha256(a["field_mask"][i, k].tobytes()).hexdigest()):
                raise ValueError("Frozen model-predicted fields/cache binding mismatch")
            if record.get("stock_code") != str(a["stock_code"][i]) or record.get("available_ordinal") != int(a["event_available_ordinal"][i, k]):
                raise ValueError("Frozen cache source identity/date mismatch")
        edges = a["edges"]
        if edges.ndim != 2 or edges.shape[0] != 2:
            raise ValueError("Invalid edge shape")
        ci, ii = edges
        if ((ci < 0) | (ci >= n)).any() or (ii < 0).any():
            raise ValueError("Edge ids out of range")
        if len(set(zip(ci, ii))) != len(ci):
            raise ValueError("Duplicate company-institution edge")
        for key in ("edge_share", "edge_age", "edge_accepted_complete"):
            if a[key].shape != ci.shape:
                raise ValueError("Edge metadata shape mismatch")
        if not a["edge_accepted_complete"].all():
            raise ValueError("Only accepted complete holdings allowed")
        _dates(a["edge_source_ordinal"], a["edge_available_ordinal"], np.ones(len(ci), bool), cutoff, "holding edge")
        for key in ("event_age", "edge_age", "edge_share", "graph_age", "body_quality", "evidence_quality", "graph_quality"):
            if not np.isfinite(a[key]).all() or (a[key] < 0).any():
                raise ValueError("Nonnegative finite metadata required: " + key)
        for key in ("body_quality", "evidence_quality", "graph_quality"):
            if (a[key] > 1).any():
                raise ValueError("Quality outside [0,1]")
        pred, supervised, labels = a["prediction_member"], a["supervised"], a["label_bin"]
        known = labels >= 0
        candidate, certified = a["supervised_candidate"], a["supervised_certified"]
        if (candidate & (~pred | ~known)).any() or (certified & ~candidate).any():
            raise ValueError("Candidate/certified supervision is not valid target membership")
        expected_supervision = candidate if self.manifest["scope"] == "synthetic_cpu_only" else certified
        if not np.array_equal(supervised, expected_supervision):
            raise ValueError("Training supervision cannot silently promote candidate outcomes")
        if self.manifest["scope"] == "candidate_panel_not_certified" and certified.any():
            raise ValueError("Candidate panel cannot certify supervision")
        if not np.isin(labels, [-1, 0, 1, 2, 3]).all() or (supervised & (~pred | ~known)).any():
            raise ValueError("Invalid supervision/label membership")
        if self.manifest["scope"] == "candidate_panel_not_certified" and (a["outcome_status"] == "known").any():
            raise ValueError("Unverified real outcomes must retain candidate status")
        observed_status = np.isin(a["outcome_status"], ["known", "complete_raw_return_candidate"])
        if (known & ~pred).any() or (known != observed_status).any():
            raise ValueError("Neighbor/future-unknown labels must not masquerade as known targets")
        ends = a["outcome_end_ordinal"]
        if (ends[known] <= cutoff).any() or (ends < 0).any() or ((ends[~known] > 0) & (ends[~known] <= cutoff)).any():
            raise ValueError("Invalid actual outcome end/status")
        observed_ends = a["observed_outcome_end_ordinal"]
        if (observed_ends[known] != ends[known]).any() or (observed_ends[~known] != 0).any():
            raise ValueError("Expected endpoint must not masquerade as an observed outcome")
        if (a["unknown_reason"][pred & ~known] == "").any():
            raise ValueError("Unknown target outcome needs an explicit reason")
        boundary = self.boundaries.get(item["split"])
        if boundary is not None and (observed_ends[known] >= boundary).any():
            raise ValueError("Unpurged actual outcome_end crosses " + item["split"] + " boundary")
        expected = {code for day, code in self.index if day == item["date"]}
        actual = set(a["stock_code"][pred])
        if actual != expected:
            raise ValueError("Prediction universe differs from hashed index members")
        for i in np.flatnonzero(pred):
            record = self.index[(item["date"], str(a["stock_code"][i]))]
            if (record["split"] != item["split"] or int(record["label_bin"]) != int(labels[i]) or
                    record["outcome_status"] != str(a["outcome_status"][i]) or
                    int(record["outcome_end_ordinal"]) != int(ends[i]) or int(record["observed_outcome_end_ordinal"]) != int(observed_ends[i]) or
                    record.get("unknown_reason", "") != str(a["unknown_reason"][i]) or bool(int(record["supervised"])) != bool(supervised[i]) or
                    bool(int(record["supervised_candidate"])) != bool(candidate[i]) or bool(int(record["supervised_certified"])) != bool(certified[i])):
                raise ValueError("Per-record index role/outcome mismatch")
