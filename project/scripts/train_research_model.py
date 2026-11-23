"""Train a temporal fold from audited one-market-day NPZ shards.

Metadata JSON has features,event_dim,structured_dim,fold and batches:
[{path,date,max_available_date,split}]. Outputs stay outside the repository.
All hyperparameters and early stopping use validation only.
"""
import argparse
import copy
import csv
import hashlib
import json
import random
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from research_model import DisclosureModel


def load_batch(item, root, device):
    if item["max_available_date"] > item["date"]:
        raise ValueError("Future information in batch " + item["date"])
    path = (root / item["path"]).resolve()
    with np.load(path, allow_pickle=False) as data:
        stock_codes = data["stock_code"].astype(str)
        batch = {k: torch.as_tensor(data[k], device=device) for k in data.files if k != "stock_code"}
    if len(set(stock_codes)) != len(stock_codes):
        raise ValueError("Duplicate stock code in batch")
    required = {"seq", "seq_mask", "events", "event_mask", "event_age", "event_quality", "edges", "edge_age", "edge_share", "graph_age", "label", "supervised", "outcome_end_ordinal"}
    if not required <= batch.keys():
        raise ValueError("Missing batch fields: " + str(required - batch.keys()))
    if not torch.isin(batch["label"][batch["supervised"].bool()], torch.tensor([0, 1], device=device)).all():
        raise ValueError("Invalid label")
    for key in ["event_age", "edge_age", "edge_share", "graph_age"]:
        if not torch.isfinite(batch[key]).all() or (batch[key] < 0).any():
            raise ValueError("Invalid " + key)
    q = batch["event_quality"]
    if not torch.isfinite(q).all() or ((q < 0) | (q > 1)).any():
        raise ValueError("Event quality must be finite in [0,1]")
    return batch, stock_codes


def predictions(model, items, root, device):
    rows = []
    model.eval()
    with torch.inference_mode():
        for item in items:
            b, codes = load_batch(item, root, device)
            out = model(b)
            p = torch.sigmoid(out["logit"]).cpu().numpy()
            y = b["label"].cpu().numpy()
            for i in np.flatnonzero(b["supervised"].cpu().numpy()):
                rows.append({"stock_code": codes[i], "date": item["date"], "label": int(y[i]), "prediction": float(p[i])})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=.001)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    outdir = args.output_dir.resolve()
    if outdir == repo or repo in outdir.parents:
        raise ValueError("Model outputs must be outside Git repository")
    manifest = json.loads(args.manifest.read_text())
    batches = sorted(manifest["batches"], key=lambda x: x["date"])
    groups = {s: [x for x in batches if x["split"] == s] for s in ["train", "validation", "test"]}
    if any(not x for x in groups.values()):
        raise ValueError("Need train, validation and test batches")
    if not max(x["date"] for x in groups["train"]) < min(x["date"] for x in groups["validation"]) <= max(x["date"] for x in groups["validation"]) < min(x["date"] for x in groups["test"]):
        raise ValueError("Overlapping temporal splits")
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    device = torch.device(args.device)
    root = args.manifest.resolve().parent
    from datetime import date
    for split, next_split in [("train", "validation"), ("validation", "test")]:
        boundary = date.fromisoformat(min(x["date"] for x in groups[next_split])).toordinal()
        for item in groups[split]:
            b, _ = load_batch(item, root, device)
            if (b["outcome_end_ordinal"][b["supervised"].bool()] >= boundary).any():
                raise ValueError("Unpurged outcome crosses " + next_split)
    model = DisclosureModel(manifest["features"], manifest["event_dim"], mode=args.mode,
                            structured_dim=manifest.get("structured_dim", 0)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=.0001)
    best_ap, best_state, stale, history = -1., None, 0, []
    for epoch in range(args.epochs):
        model.train(); losses = []
        for item in groups["train"]:
            b, _ = load_batch(item, root, device)
            optimizer.zero_grad()
            loss = model.loss(model(b), b["label"].float(), b["supervised"])
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite training loss")
            loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.); optimizer.step()
            losses.append(float(loss.detach()))
        rows = predictions(model, groups["validation"], root, device)
        ap = float(average_precision_score([r["label"] for r in rows], [r["prediction"] for r in rows]))
        history.append({"epoch": epoch + 1, "loss": float(np.mean(losses)), "validation_AP": ap})
        if ap > best_ap:
            best_ap, best_state, stale = ap, copy.deepcopy(model.state_dict()), 0
        else:
            stale += 1
        if stale >= 3:
            break
    model.load_state_dict(best_state)
    outdir.mkdir(parents=True, exist_ok=True)
    torch.save(best_state, outdir / "model.pt")
    rows = predictions(model, groups["test"], root, device)
    with (outdir / "test_predictions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stock_code", "date", "label", "prediction"]);w.writeheader();w.writerows(rows)
    report = {"seed": args.seed, "mode": args.mode, "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "fold": manifest["fold"], "best_validation_AP": best_ap, "history": history,
              "test_rows": len(rows), "torch": torch.__version__, "device": str(device),
              "test_AP": float(average_precision_score([r["label"] for r in rows], [r["prediction"] for r in rows]))}
    (outdir / "run_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
