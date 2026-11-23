"""Profile disclosure grouping evidence; rank completeness is not a PIT certificate.

The report contains only counts. Source holder names/ids and rows stay private.
SHSer is profiled as a row field, never assumed to identify a shareholder-list type.
"""
import argparse
import csv
import gzip
import json
import time
from collections import Counter
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    start = time.perf_counter()
    missing = Counter(); updates = Counter(); series = Counter(); sources = Counter(); groups = {}
    rows = valid = 0
    with gzip.open(a.input, "rt", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"stock_code", "report_date", "publication_date", "info_source", "holder_series", "update_date", "holder_rank", "pct_total"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("Missing disclosure fields: " + str(required - set(reader.fieldnames or [])))
        for r in reader:
            rows += 1
            for k in required:
                if not r[k]: missing[k] += 1
            sources[r["info_source"]] += 1
            series[r["holder_series"]] += 1
            updates[r["update_date"][:4]] += 1
            if not r["stock_code"] or not r["publication_date"] or not r["report_date"] or not r["info_source"]:
                continue
            try: rank = int(float(r["holder_rank"]))
            except ValueError: continue
            if not 1 <= rank <= 10: continue
            key = (r["stock_code"], r["report_date"], r["publication_date"], r["info_source"])
            # Store only number of rows, rank bitmask and duplicate-rank count.
            count, mask, duplicates = groups.get(key, (0, 0, 0))
            bit = 1 << (rank - 1)
            groups[key] = (count + 1, mask | bit, duplicates + bool(mask & bit))
            valid += 1
    counts = Counter(v[0] for v in groups.values())
    report = {"scope": "aggregate_disclosure_group_evidence_not_full_list_or_vintage_certificate",
              "rows": rows, "top10_rows_with_group_keys": valid, "missing_counts": dict(missing),
              "holder_series_distinct_values": len(series), "holder_series_top_value_counts": dict(series.most_common(10)),
              "source_distinct_values": len(sources), "rows_by_update_year": dict(sorted(updates.items())),
              "candidate_groups_company_report_publication_source": len(groups),
              "groups_with_duplicate_ranks": sum(v[2] > 0 for v in groups.values()),
              "groups_with_exactly_ten_unique_ranks": sum(v[0] == 10 and v[1] == 1023 for v in groups.values()),
              "candidate_group_size_counts": dict(sorted(counts.items())),
              "main_graph_ready": False,
              "remaining_checks": ["Interpret SHSer and list types using source dictionary", "Resolve duplicate ranks and list-basis mixtures", "Verify complete public disclosures and revision availability", "Audit institution identities and shared-company coverage"],
              "elapsed_seconds": time.perf_counter() - start}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
