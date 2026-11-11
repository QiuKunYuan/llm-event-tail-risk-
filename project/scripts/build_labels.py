#!/usr/bin/env python3
"""Run the repository's existing C++-accelerated label builder from this project."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
DEFAULT_DATA_ROOT = Path("/home/admin/workspace/RESSET论文数据")
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "processed" / "tail_risk_labels.csv.gz"
BUILDER = REPO_ROOT / "scripts" / "build_tail_risk_labels.py"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not BUILDER.is_file():
        raise FileNotFoundError(f"Existing label implementation not found: {BUILDER}")
    command = [
        sys.executable,
        str(BUILDER),
        "--data-root",
        str(args.data_root.expanduser().resolve()),
        "--output",
        str(args.output.expanduser().resolve()),
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()

