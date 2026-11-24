"""Compare every decompressed CSV byte of existing and rebuilt label panels."""
import argparse
import gzip
import hashlib
from pathlib import Path

from validation_io import environment, fingerprints, new_private_directory, unchanged, write_json


def content_digest(path):
    digest = hashlib.sha256()
    size = lines = 0
    with gzip.open(path, "rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
            size += len(block)
            lines += block.count(b"\n")
    return {"uncompressed_sha256": digest.hexdigest(), "uncompressed_bytes": size, "lines_including_header": lines}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--existing", type=Path, required=True)
    p.add_argument("--rebuilt", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    out = new_private_directory(a.output_dir)
    before = fingerprints([a.existing, a.rebuilt, Path(__file__), Path(__file__).with_name("validation_io.py")])
    old, new = content_digest(a.existing), content_digest(a.rebuilt)
    unchanged(before)
    report = {"scope": "full_panel_reproduction_by_production_algorithm_not_independent_reference",
              "existing": old, "rebuilt": new, "identical_decompressed_content": old == new,
              "input_and_code_sha256": before, "environment": environment()}
    write_json(out / "comparison_report.json", report)
    print({"identical_decompressed_content": old == new, "csv_lines": old["lines_including_header"]})
    if old != new:
        raise RuntimeError("Rebuilt label panel differs; do not certify old labels")


if __name__ == "__main__":
    main()
