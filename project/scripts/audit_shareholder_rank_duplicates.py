"""Externally sort private group rows to explain duplicate-rank evidence.

Only aggregate counts leave the scratch directory. Identical names are evidence,
not entity certification; SHSer/UpDt never become list-type/public-time labels.
No selection of a latest revision or arbitrary ten rows is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from validation_io import (csv_rows, environment, fingerprints, new_private_directory,
                           number, unchanged, write_json)

FIELDS = ("holder_id", "holder_name", "holder_attribute", "holder_nature", "holder_type",
          "total_shares", "float_shares", "holding_shares", "pct_total", "pct_float",
          "holder_series", "update_date")
GROUP_FIELDS = ("stock_code", "report_date", "publication_date", "info_source")


def digest_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def profile_group(records):
    """Counts are group/rank units, not distinct certified disclosures."""
    by_rank = defaultdict(list)
    for rank, payload in records:
        by_rank[rank].append(payload)
    duplicated = {k: v for k, v in by_rank.items() if len(v) > 1}
    counters = Counter()
    changes = Counter()
    counters["duplicate_rank_units"] = len(duplicated)
    counters["groups_with_duplicate_ranks"] = bool(duplicated)
    counters["groups_exactly_20_rows_two_at_each_rank_1_to_10"] = (
        len(records) == 20 and set(by_rank) == set(range(1, 11)) and all(len(v) == 2 for v in by_rank.values()))
    group_has_identity_conflict = False
    for payloads in duplicated.values():
        sets = {f: {p[f] for p in payloads} for f in FIELDS}
        for f, values in sets.items():
            changes[f] += len(values) > 1
        same_name = len(sets["holder_name"]) == 1 and "" not in sets["holder_name"]
        same_id = len(sets["holder_id"]) == 1 and "" not in sets["holder_id"]
        counters["duplicate_rank_units_same_nonempty_name"] += same_name
        counters["duplicate_rank_units_same_nonempty_id"] += same_id
        counters["duplicate_rank_units_missing_any_id"] += "" in sets["holder_id"]
        counters["duplicate_rank_units_different_names"] += len(sets["holder_name"]) > 1
        counters["duplicate_rank_units_different_nonempty_ids"] += len(sets["holder_id"] - {""}) > 1
        counters["duplicate_rank_units_exact_repeats_in_retained_fields"] += all(len(v) == 1 for v in sets.values())
        counters["duplicate_rank_units_identical_except_series_update"] += all(
            len(v) == 1 for f, v in sets.items() if f not in {"holder_series", "update_date"})
        counters["duplicate_rank_units_holding_or_percentage_difference"] += any(
            len(sets[f]) > 1 for f in ("holding_shares", "pct_total", "pct_float"))
        group_has_identity_conflict |= len(sets["holder_name"]) > 1 or len(sets["holder_id"] - {""}) > 1
    counters["groups_with_identity_field_conflict_at_same_rank"] = group_has_identity_conflict
    # Does adding a raw row field remove repeated ranks? This is not a certification rule.
    for field in ("holder_series", "update_date", "holder_type"):
        bins = [(p[field], rank) for rank, p in records]
        counters["duplicate_groups_with_unique_ranks_after_splitting_" + field] = bool(duplicated) and len(bins) == len(set(bins))
    return counters, changes


def profile_basis(row, counts):
    hold = number(row["holding_shares"])
    for name, denominator, observed in (("total", "total_shares", "pct_total"),
                                         ("float", "float_shares", "pct_float")):
        den, pct = number(row[denominator]), number(row[observed])
        if math.isfinite(hold) and math.isfinite(den) and den > 0 and math.isfinite(pct):
            counts[name + "_basis_numeric_rows"] += 1
            # Compare both unit hypotheses; column names do not certify the scale.
            ratio = hold / den
            counts[name + "_basis_matches_fraction_scale_1e_6"] += abs(ratio - pct) <= 1e-6
            counts[name + "_basis_matches_percent_scale_1e_4pp"] += abs(100 * ratio - pct) <= 1e-4
            counts[name + "_basis_observed_values_between_0_and_1"] += 0 <= pct <= 1
            counts[name + "_basis_observed_values_greater_than_1"] += pct > 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--prior-audit", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    start = time.perf_counter()
    out = new_private_directory(a.output_dir)
    inputs = [a.input, Path(__file__), Path(__file__).with_name("validation_io.py")]
    if a.prior_audit:
        inputs.append(a.prior_audit)
    before = fingerprints(inputs)
    counters, basis, changes, sizes, years = Counter(), Counter(), Counter(), Counter(), Counter()
    with tempfile.TemporaryDirectory(prefix="private-rank-", dir=out) as temp:
        scratch = Path(temp)
        raw, sorted_rows = scratch / "groups.tsv", scratch / "groups-sorted.tsv"
        with raw.open("w", encoding="utf-8", newline="") as destination:
            for row in csv_rows(a.input):
                counters["rows"] += 1
                required = set(FIELDS) | set(GROUP_FIELDS) | {"holder_rank"}
                if not required <= set(row):
                    raise ValueError("Missing original shareholder fields")
                rank_value = number(row["holder_rank"])
                if (not all(row[k] for k in GROUP_FIELDS) or not math.isfinite(rank_value) or
                    not rank_value.is_integer() or not 1 <= rank_value <= 10):
                    counters["rows_excluded_from_candidate_groups"] += 1
                    continue
                rank = int(rank_value)
                counters["top10_rows_with_group_keys"] += 1
                series = number(row["holder_series"])
                if math.isfinite(series):
                    counters["numeric_holder_series_rows"] += 1
                    counters["holder_series_equals_rank_rows"] += series == rank
                years[row["update_date"][:4]] += 1
                profile_basis(row, basis)
                key = digest_text(json.dumps([row[k] for k in GROUP_FIELDS], ensure_ascii=True, separators=(",", ":")))
                payload = {f: row[f] for f in FIELDS}
                for f in ("holder_id", "holder_name"):
                    payload[f] = digest_text(payload[f])
                encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                destination.write(key + "\t" + str(rank) + "\t" + encoded + "\n")
        with sorted_rows.open("wb") as destination:
            env = dict(os.environ, LC_ALL="C")
            subprocess.run(["sort", "-S", "512M", "--parallel=2", "--temporary-directory=" + str(scratch),
                            "-t", "\t", "-k1,1", str(raw)], stdout=destination, env=env, check=True)
        def rows():
            with sorted_rows.open(encoding="utf-8") as source:
                for line in source:
                    key, rank, encoded = line.rstrip("\n").split("\t", 2)
                    yield key, int(rank), json.loads(encoded)
        for _, group in itertools.groupby(rows(), key=lambda r: r[0]):
            records = [(rank, payload) for _, rank, payload in group]
            counters["candidate_groups_company_report_publication_source"] += 1
            sizes[len(records)] += 1
            c, changed = profile_group(records)
            counters.update(c)
            changes.update(changed)
    unchanged(before)
    prior_matches = None
    if a.prior_audit:
        prior = json.loads(a.prior_audit.read_text(encoding="utf-8"))
        prior_matches = all(counters[k] == prior[k] for k in
                            ("rows", "top10_rows_with_group_keys", "candidate_groups_company_report_publication_source", "groups_with_duplicate_ranks"))
    report = {"scope": "aggregate_duplicate_rank_evidence_not_list_type_entity_or_vintage_certificate",
              "counts": dict(counters), "candidate_group_size_counts": dict(sorted(sizes.items())),
              "duplicate_rank_units_with_field_changes": dict(changes), "percentage_basis_checks": dict(basis),
              "candidate_rows_by_update_year": dict(sorted(years.items())), "prior_audit_counts_match": prior_matches,
              "identity_comparison": "exact original nonempty id/name equality via SHA256; no automatic name merge",
              "main_graph_ready": False,
              "remaining_checks": ["Original list types and duplicate origins require dictionary or public source evidence",
                                   "No retained field alone certifies completeness or historical vintage",
                                   "Institution identity and product/manager distinction require separate audit"],
              "input_and_code_sha256": before, "environment": environment(),
              "elapsed_seconds": time.perf_counter() - start}
    write_json(out / "duplicate_rank_report.json", report)
    print({"counts": dict(counters), "field_changes": dict(changes), "prior_audit_counts_match": prior_matches})
    if prior_matches is False:
        raise RuntimeError("Candidate grouping no longer reproduces prior aggregate audit")


if __name__ == "__main__":
    main()
