#!/usr/bin/env python3
"""Primary label entry point: nullable actual-loss R5 candidates in a NEW private directory.

Required arguments are --returns, --calendar and --output-dir. This entry point
no longer invokes the residual-label C++ builder or its old processed outputs.
Legacy residual source, tables and indices remain untouched diagnostic evidence.
Use build_loss_index.py and verify_loss_index.py for the new immutable four-fold
index/readback. Complete raw windows are not certified total-return labels.
"""
from build_loss_panel import main


if __name__ == "__main__":
    main()
